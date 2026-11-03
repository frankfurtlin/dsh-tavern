import {ContractError} from '../domain/local-contract'
export interface EpubChapter {title:string;text:string}
const decoder=new TextDecoder('utf-8',{fatal:true}),limit=100*1024*1024
function fail(message:string):never{throw new ContractError('EPUB_INVALID',message+'；原文件保留')}
function path(value:string,base=''){
  if(!value||/[\0\\]/.test(value)||/^(?:[a-z][a-z0-9+.-]*:|\/)/i.test(value)||value.includes('?'))fail('EPUB 包含无效路径')
  let decoded:string;try{decoded=decodeURIComponent(value.split('#')[0]!)}catch{fail('EPUB 路径编码无效')}
  if(/[\0\\]/.test(decoded)||decoded.startsWith('/'))fail('EPUB 路径无效')
  const parts=base.split('/').filter(Boolean);for(const part of decoded.split('/')){if(part==='..'){if(!parts.length)fail('EPUB 路径超出书包');parts.pop()}else if(part&&part!=='.')parts.push(part)}return parts.join('/')
}
function xml(bytes:Uint8Array){let source:string;try{source=decoder.decode(bytes)}catch{fail('EPUB XML 编码无效')};if(/<!ENTITY|<!DOCTYPE[^>]*\[/i.test(source))fail('EPUB 不接受自定义实体');const document=new DOMParser().parseFromString(source,'application/xml');if(document.querySelector('parsererror'))fail('EPUB XML 无法解析');return document}
function crc32(bytes:Uint8Array){let crc=0xffffffff;for(const byte of bytes){crc^=byte;for(let bit=0;bit<8;bit++)crc=crc>>>1^(crc&1?0xedb88320:0)}return (crc^0xffffffff)>>>0}
/** Bounded ZIP extraction. No package resource is fetched from the network. */
export async function parseLocalEpub(bytes:Uint8Array,signal:AbortSignal):Promise<{title:string;chapters:EpubChapter[]}>{
  const view=new DataView(bytes.buffer,bytes.byteOffset,bytes.byteLength),u16=(offset:number)=>view.getUint16(offset,true),u32=(offset:number)=>view.getUint32(offset,true);let end=-1
  if(bytes.length>limit||bytes.length<22)fail('EPUB 大小无效')
  for(let at=bytes.length-22;at>=Math.max(0,bytes.length-65557);at--)if(u32(at)===0x06054b50&&at+22+u16(at+20)===bytes.length){end=at;break}
  if(end<0||u16(end+4)||u16(end+6)||u16(end+8)!==u16(end+10)||u16(end+10)===65535)fail('EPUB 必须是单卷 ZIP')
  const count=u16(end+10),centralSize=u32(end+12),centralAt=u32(end+16);if(count>30000||centralAt+centralSize!==end)fail('EPUB 目录大小无效')
  const entries=new Map<string,{method:number;size:number;compressed:number;offset:number;crc:number;flags:number}>();let cursor=centralAt,total=0
  for(let index=0;index<count;index++){
    signal.throwIfAborted();if(cursor+46>end||u32(cursor)!==0x02014b50)fail('EPUB 目录损坏')
    const nameSize=u16(cursor+28),extra=u16(cursor+30),comment=u16(cursor+32),next=cursor+46+nameSize+extra+comment;if(next>end||u16(cursor+34))fail('EPUB 目录损坏')
    let name:string;try{name=decoder.decode(bytes.subarray(cursor+46,cursor+46+nameSize))}catch{fail('EPUB 文件名不是 UTF-8')};if(name.endsWith('/')){cursor=next;continue}const normalized=path(name);if(normalized!==name||entries.has(name))fail('EPUB 存在重复或不规范路径')
    const size=u32(cursor+24),compressed=u32(cursor+20),offset=u32(cursor+42),flags=u16(cursor+8),method=u16(cursor+10);total+=size;if(flags&1||size===0xffffffff||compressed===0xffffffff||offset===0xffffffff||![0,8].includes(method)||total>limit||compressed>limit||offset>=centralAt)fail('EPUB 压缩方式或展开容量不受支持')
    entries.set(name,{method,size,compressed,offset,crc:u32(cursor+16),flags});cursor=next
  }
  if(cursor!==end||entries.has('META-INF/encryption.xml'))fail('EPUB 目录不完整或含 DRM')
  const read=async(name:string)=>{
    signal.throwIfAborted();const row=entries.get(name);if(!row)fail('EPUB 引用的文件不存在：'+name)
    const at=row.offset;if(at+30>centralAt||u32(at)!==0x04034b50||u16(at+8)!==row.method||u16(at+6)!==row.flags)fail('EPUB 文件头不一致')
    const start=at+30+u16(at+26)+u16(at+28);if(start+row.compressed>centralAt)fail('EPUB 文件越界')
    let localName:string;try{localName=decoder.decode(bytes.subarray(at+30,at+30+u16(at+26)))}catch{fail('EPUB 本地文件名无效')};if(localName!==name)fail('EPUB 文件名不一致')
    const source=bytes.slice(start,start+row.compressed);let result:Uint8Array
    if(row.method===0)result=source
    else{
      let stream:ReadableStream<Uint8Array>;try{const Inflate=DecompressionStream as unknown as new(format:string)=>DecompressionStream;stream=new Blob([source]).stream().pipeThrough(new Inflate('deflate-raw'))}catch{throw new ContractError('EPUB_DECOMPRESSION_UNAVAILABLE','当前浏览器不能展开 EPUB，请更新浏览器或使用服务器导入')}
      const reader=stream.getReader(),chunks:Uint8Array[]=[];let length=0;try{while(true){signal.throwIfAborted();const next=await reader.read();if(next.done)break;length+=next.value.length;if(length>row.size||length>limit){await reader.cancel();fail('EPUB 展开超过声明容量')}chunks.push(next.value)}result=new Uint8Array(length);let offset=0;for(const chunk of chunks){result.set(chunk,offset);offset+=chunk.length}}finally{await reader.cancel().catch(()=>{})}
    }
    if(result.length!==row.size||crc32(result)!==row.crc)fail('EPUB 文件完整性校验失败');return result
  }
  const mime=entries.get('mimetype');if(!mime||mime.method!==0||mime.offset!==0||decoder.decode(await read('mimetype'))!=='application/epub+zip')fail('文件不是有效 EPUB')
  const container=xml(await read('META-INF/container.xml')),root=Array.from(container.getElementsByTagNameNS('*','rootfile')).find(item=>item.getAttribute('media-type')==='application/oebps-package+xml');if(!root)fail('EPUB 缺少 OPF')
  const opfName=path(root.getAttribute('full-path')||''),base=opfName.split('/').slice(0,-1).join('/'),opf=xml(await read(opfName)),manifest=new Map<string,Element>()
  for(const item of Array.from(opf.getElementsByTagNameNS('*','item'))){const identifier=item.getAttribute('id');if(!identifier||manifest.has(identifier))fail('EPUB manifest ID 无效');manifest.set(identifier,item)}
  const spine=Array.from(opf.getElementsByTagNameNS('*','itemref'));if(!spine.length||spine.length>10000)fail('EPUB 章节数量无效')
  const chapters:EpubChapter[]=[];let characters=0
  for(const ref of spine){signal.throwIfAborted();const item=manifest.get(ref.getAttribute('idref')||'');if(!item||!['application/xhtml+xml','text/html'].includes(item.getAttribute('media-type')||''))fail('EPUB spine 引用无效');const document=xml(await read(path(item.getAttribute('href')||'',base))),body=document.getElementsByTagNameNS('*','body')[0];if(!body)fail('EPUB 章节缺少 body')
    const lines:string[]=[];let buffer='';const flush=()=>{if(buffer.trim())lines.push(buffer);buffer=''}
    const walk=(node:Node)=>{if(node.nodeType===Node.TEXT_NODE){buffer+=node.textContent||'';return}if(node.nodeType!==Node.ELEMENT_NODE)return;const tag=(node as Element).localName.toLowerCase();if(['script','style','iframe','object','embed'].includes(tag))return;if(tag==='br'){flush();return}const block=['p','div','section','article','blockquote','li','pre','h1','h2','h3','h4','h5','h6'].includes(tag);if(block)flush();for(const child of Array.from(node.childNodes))walk(child);if(block)flush()};walk(body);flush()
    const title=Array.from(body.getElementsByTagNameNS('*','h1')).concat(Array.from(body.getElementsByTagNameNS('*','h2')))[0]?.textContent?.trim()||'第 '+(chapters.length+1)+' 章';const text=lines.join('\n');characters+=Array.from(text).length;if(characters>3_000_000)fail('EPUB 正文超过 300 万字符');chapters.push({title:title.slice(0,1000),text});await new Promise<void>(resolve=>setTimeout(resolve,0))
  }
  if(!chapters.some(chapter=>chapter.text.trim()))fail('EPUB 没有可阅读正文');return {title:opf.getElementsByTagNameNS('*','title')[0]?.textContent?.trim()||'',chapters}
}
