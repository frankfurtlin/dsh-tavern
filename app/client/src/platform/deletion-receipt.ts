export type DeletionReceipt={id:string;base:string}
type StoredReceipt=DeletionReceipt&{recordedAt?:number}
type Storage={readLocal:()=>string|null;writeLocal:(value:string)=>void;readNative?:()=>Promise<string>;writeNative?:(value:string)=>Promise<void>;now?:()=>number}

function parse(value:string|null):StoredReceipt|null{
  try{
    const data=JSON.parse(value||'null')
    if(!data||typeof data!=='object'||typeof data.id!=='string'||!data.id||typeof data.base!=='string')return null
    const recordedAt=Number.isSafeInteger(data.recordedAt)&&data.recordedAt>0?data.recordedAt:undefined
    return{id:data.id,base:data.base,...(recordedAt?{recordedAt}:{})}
  }catch{return null}
}

// Native and WebView copies can finish independently. Keep the latest valid
// receipt available even when a native write fails or an older write completes.
export function createDeletionReceiptStore(storage:Storage){
  let recordedAt=0,nativeWork:Promise<void>=Promise.resolve(),queuedNative:string|null=null
  const local=()=>{try{return parse(storage.readLocal())}catch{return null}}
  function writeNative(value:string){
    if(value===queuedNative)return nativeWork
    queuedNative=value
    const work=nativeWork.catch(()=>{}).then(()=>storage.writeNative!(value))
    nativeWork=work
    const finished=()=>{if(nativeWork===work)queuedNative=null}
    void work.then(finished,finished)
    return work
  }
  async function load():Promise<DeletionReceipt|null>{
    let file:StoredReceipt|null=null
    if(storage.readNative){try{file=parse(await storage.readNative())}catch{/* The independent local copy can still be usable. */}}
    // A response may be saved while the independent native read is pending.
    const web=local()
    const latest=!file?web:!web?file:(web.recordedAt||0)>(file.recordedAt||0)?web:file
    recordedAt=Math.max(recordedAt,web?.recordedAt||0,file?.recordedAt||0)
    if(latest){
      const value=JSON.stringify(latest)
      // Android may restore an older WebView storage snapshot after force-stop.
      // Reconcile from the durable copy before the caller queries its status.
      if(JSON.stringify(web)!==value)try{storage.writeLocal(value)}catch{/* Recovery still returns the independently valid receipt. */}
      if(storage.writeNative&&JSON.stringify(file)!==value)void writeNative(value).catch(()=>{})
    }
    return latest?{id:latest.id,base:latest.base}:null
  }
  async function save(receipt:DeletionReceipt){
    if(typeof receipt.id!=='string'||!receipt.id||typeof receipt.base!=='string')throw new Error('清理回执不完整')
    recordedAt=Math.max((storage.now||Date.now)(),recordedAt+1,(local()?.recordedAt||0)+1)
    const value=JSON.stringify({id:receipt.id,base:receipt.base,recordedAt})
    let localSaved=false,nativeSaved=false
    // Persist the response before the first native await; auth cleanup can
    // navigate away while native IO is still completing.
    try{storage.writeLocal(value);localSaved=true}catch{/* Still try the native copy. */}
    if(storage.writeNative){
      const work=writeNative(value)
      try{await work;nativeSaved=true}catch{/* Retain the latest local copy. */}
    }
    if(!localSaved&&!nativeSaved)throw new Error('本机无法保存清理回执')
    return{localSaved,nativeSaved}
  }
  return{load,save}
}
