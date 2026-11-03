import {ContractError} from '../domain/local-contract'
const utf8=new TextDecoder('utf-8',{fatal:true})
export const crc32=(bytes:Uint8Array)=>{let crc=0xffffffff;for(const byte of bytes){crc^=byte;for(let bit=0;bit<8;bit++)crc=crc>>>1^(crc&1?0xedb88320:0)}return (crc^0xffffffff)>>>0}
function fail(message:string):never{throw new ContractError('ARCHIVE_INVALID',message+'；原文件保留')}
export function archivePath(name:string,decodeUri:boolean|'canonical'=false){
  if(!name||name.length>1000||/[\0\\]/.test(name)||/^(?:[a-z][a-z0-9+.-]*:|\/)/i.test(name))fail('包内路径无效')
  let decoded:string;try{decoded=decodeURIComponent(name)}catch{if(decodeUri===true)fail('包内路径编码无效');decoded=name}
  if(/[\0\\]/.test(decoded)||/^(?:[a-z][a-z0-9+.-]*:|\/)/i.test(decoded)||decoded.split('/').some(part=>part==='..'||part==='.'||!part))fail('包内路径越界或不规范')
  return decodeUri?decoded:name
}
export async function inflateBounded(bytes:Uint8Array,format:'deflate'|'deflate-raw',max:number,signal:AbortSignal){
  signal.throwIfAborted();const Inflate=DecompressionStream as unknown as new(format:string)=>DecompressionStream
  let stream:ReadableStream<Uint8Array>;try{stream=new Blob([Uint8Array.from(bytes)]).stream().pipeThrough(new Inflate(format))}catch{throw new ContractError('ARCHIVE_DECOMPRESSION_UNAVAILABLE','当前浏览器不能展开此文件')}
  const reader=stream.getReader(),chunks:Uint8Array[]=[];let size=0;const abort=()=>void reader.cancel(signal.reason).catch(()=>{});signal.addEventListener('abort',abort,{once:true})
  try{while(true){signal.throwIfAborted();const next=await reader.read();signal.throwIfAborted();if(next.done)break;size+=next.value.length;if(size>max)fail('展开容量超过限额');chunks.push(next.value)}const result=new Uint8Array(size);let offset=0;for(const chunk of chunks){result.set(chunk,offset);offset+=chunk.length}return result}
  finally{signal.removeEventListener('abort',abort);await reader.cancel().catch(()=>{})}
}
/** A strict, bounded ZIP reader: no extraction to paths, no external requests. */
export async function readBoundedZip(bytes:Uint8Array,signal:AbortSignal,limit=100*1024*1024):Promise<Map<string,Uint8Array>>{
  if(bytes.length<22||bytes.length>limit)fail('压缩包容量无效')
  const view=new DataView(bytes.buffer,bytes.byteOffset,bytes.byteLength),u16=(at:number)=>view.getUint16(at,true),u32=(at:number)=>view.getUint32(at,true);let end=-1
  for(let at=bytes.length-22;at>=Math.max(0,bytes.length-65557);at--)if(u32(at)===0x06054b50&&at+22+u16(at+20)===bytes.length){end=at;break}
  if(end<0||u16(end+4)||u16(end+6)||u16(end+8)!==u16(end+10)||u16(end+10)===65535)fail('必须使用单卷非 ZIP64 包')
  const count=u16(end+10),centralAt=u32(end+16),centralSize=u32(end+12);if(!count||count>10000||centralAt+centralSize!==end)fail('压缩包目录无效')
  const result=new Map<string,Uint8Array>(),normalizedPaths=new Set<string>(),ranges:{start:number;end:number}[]=[];let cursor=centralAt,total=0
  for(let index=0;index<count;index++){
    signal.throwIfAborted();if(cursor+46>end||u32(cursor)!==0x02014b50)fail('压缩包目录损坏')
    const flags=u16(cursor+8),method=u16(cursor+10),size=u32(cursor+24),compressed=u32(cursor+20),offset=u32(cursor+42),nameSize=u16(cursor+28),next=cursor+46+nameSize+u16(cursor+30)+u16(cursor+32)
    if(next>end||u16(cursor+34)||flags&1||flags&0x40||![0,8].includes(method)||[size,compressed,offset].includes(0xffffffff))fail('不支持加密或此压缩格式')
    let name:string;try{name=utf8.decode(bytes.subarray(cursor+46,cursor+46+nameSize))}catch{fail('文件名不是 UTF-8')}
    const directory=name.endsWith('/');archivePath(directory?name.slice(0,-1):name)
    const normalized=archivePath(directory?name.slice(0,-1):name,'canonical')+(directory?'/':'');if(result.has(name)||normalizedPaths.has(normalized))fail('存在重复或编码歧义的包内路径');normalizedPaths.add(normalized)
    total+=size;if(total>limit||compressed>limit||offset+30>centralAt||u32(offset)!==0x04034b50||u16(offset+6)!==flags||u16(offset+8)!==method)fail('容量超限或文件头不一致')
    const localNameSize=u16(offset+26),start=offset+30+localNameSize+u16(offset+28),finish=start+compressed
    if(finish>centralAt)fail('文件越界')
    let localName:string;try{localName=utf8.decode(bytes.subarray(offset+30,offset+30+localNameSize))}catch{fail('文件名无效')}
    if(localName!==name||!(flags&8)&&(u32(offset+18)!==compressed||u32(offset+22)!==size||u32(offset+14)!==u32(cursor+16)))fail('文件头与目录不一致')
    if(ranges.some(row=>offset<row.end&&finish>row.start))fail('包内文件区间重叠');ranges.push({start:offset,end:finish})
    const data=method===0?bytes.slice(start,finish):await inflateBounded(bytes.slice(start,finish),'deflate-raw',size,signal)
    if(data.length!==size||crc32(data)!==u32(cursor+16))fail('文件完整性校验失败：'+name)
    if(directory&&size)fail('目录包含文件正文')
    result.set(name,data);cursor=next
    if(index%16===0)await new Promise<void>(resolve=>setTimeout(resolve,0))
  }
  if(cursor!==end)fail('目录长度不一致');return result
}
/** Stored ZIP export preserves every entry, including unknown application data. */
export function writeStoredZip(files:Map<string,Uint8Array>,limit=100*1024*1024){
  if(!files.size||files.size>10000)fail('导出条目数无效')
  const local:Uint8Array[]=[],central:Uint8Array[]=[];let offset=0,centralSize=0
  for(const [name,bytes] of files){archivePath(name.endsWith('/')?name.slice(0,-1):name);const label=new TextEncoder().encode(name);if(label.length>65535)fail('文件名超限')
    const checksum=crc32(bytes),header=new Uint8Array(30+label.length),v=new DataView(header.buffer);v.setUint32(0,0x04034b50,true);v.setUint16(4,20,true);v.setUint16(6,0x800,true);v.setUint32(14,checksum,true);v.setUint32(18,bytes.length,true);v.setUint32(22,bytes.length,true);v.setUint16(26,label.length,true);header.set(label,30)
    const entry=new Uint8Array(46+label.length),c=new DataView(entry.buffer);c.setUint32(0,0x02014b50,true);c.setUint16(4,20,true);c.setUint16(6,20,true);c.setUint16(8,0x800,true);c.setUint32(16,checksum,true);c.setUint32(20,bytes.length,true);c.setUint32(24,bytes.length,true);c.setUint16(28,label.length,true);c.setUint32(42,offset,true);entry.set(label,46)
    local.push(header,bytes);central.push(entry);offset+=header.length+bytes.length;centralSize+=entry.length;if(offset+centralSize+22>limit)fail('导出包超过容量限额')
  }
  const ending=new Uint8Array(22),e=new DataView(ending.buffer);e.setUint32(0,0x06054b50,true);e.setUint16(8,files.size,true);e.setUint16(10,files.size,true);e.setUint32(12,centralSize,true);e.setUint32(16,offset,true)
  const output=new Uint8Array(offset+centralSize+22);let at=0;for(const chunk of [...local,...central,ending]){output.set(chunk,at);at+=chunk.length}return output
}
