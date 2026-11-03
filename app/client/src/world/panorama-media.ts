// Shared progressive media lifecycle for the live scene and isolated experiments.
// Narrative identity and image revision have separate lifetimes.
export interface PanoramaRevision {
  scope:string
  revision:string
  url:string
  interactionsVerified:boolean
}
export interface PanoramaSurface {
  replace(url:string):Promise<boolean>
  destroy():void
}
export interface PanoramaMediaState {
  phase:'loading'|'ready'|'retained'|'failed'|'empty'
  shown:PanoramaRevision|null
  interactionsEnabled:boolean
}
export class PanoramaMediaController {
  private epoch=0
  private scope=''
  private surface:PanoramaSurface|null=null
  private shown:PanoramaRevision|null=null
  private objectUrl=''
  private pending:AbortController|null=null
  private swaps:Promise<void>=Promise.resolve()
  constructor(private create:(url:string)=>Promise<PanoramaSurface>,
    private changed:(state:PanoramaMediaState)=>void,
    private download:(url:string,signal:AbortSignal)=>Promise<Blob>=async(url,signal)=>{
      const response=await fetch(url,{signal,credentials:'same-origin'})
      if(!response.ok)throw new Error('图片读取失败')
      return response.blob()
    }){}
  private emit(phase:PanoramaMediaState['phase'],enabled=false){
    this.changed({phase,shown:this.shown,interactionsEnabled:enabled})
  }
  private clear(){
    this.surface?.destroy();this.surface=null;this.shown=null
    if(this.objectUrl)URL.revokeObjectURL(this.objectUrl)
    this.objectUrl=''
  }
  async show(value:PanoramaRevision):Promise<boolean>{
    const generation=++this.epoch
    this.pending?.abort()
    if(this.scope!==value.scope){this.clear();this.scope=value.scope}
    if(this.shown?.revision===value.revision){
      this.shown={...value};this.emit('ready',value.interactionsVerified);return true
    }
    this.pending=new AbortController()
    this.emit('loading')
    let url=''
    try{
      const blob=await this.download(value.url,this.pending.signal)
      if(generation!==this.epoch)return false
      url=URL.createObjectURL(blob)
      let completed=false
      const swap=async()=>{
        if(generation!==this.epoch)return
        const previous=this.surface
        if(previous){
          try{
            if(!await previous.replace(url))throw new Error('图片替换中止')
          }catch(error){
            // A failed texture upload may have touched the renderer. Restore the
            // previous bytes, not merely the previous revision label.
            if(generation===this.epoch&&this.objectUrl){
              try{if(!await previous.replace(this.objectUrl))this.clear()}catch{this.clear()}
            }
            throw error
          }
        }else{
          const created=await this.create(url)
          if(generation!==this.epoch){created.destroy();return}
          this.surface=created
        }
        if(generation!==this.epoch)return
        const old=this.objectUrl;this.objectUrl=url;url=''
        this.shown={...value};completed=true
        if(old)URL.revokeObjectURL(old)
        this.emit('ready',value.interactionsVerified)
      }
      const job=this.swaps.then(swap,swap)
      this.swaps=job.catch(()=>{})
      await job
      return completed
    }catch{
      if(generation===this.epoch)this.emit(this.shown?'retained':'failed',Boolean(this.shown?.interactionsVerified))
      return false
    }finally{
      if(url)URL.revokeObjectURL(url)
      if(generation===this.epoch)this.pending=null
    }
  }
  dispose(){this.epoch++;this.pending?.abort();this.pending=null;this.clear();this.scope='';this.emit('empty')}
}
