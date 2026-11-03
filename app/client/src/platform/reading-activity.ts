import {onMounted,onUnmounted,watch} from 'vue'
import {api,authGeneration,newKey} from '../api'
import {accountId,appForeground,native} from './bridge'

export interface ReadingContext {bookId:string;branchId:string;revisionId:string;enabled:boolean}

// Count visible, focused reading only. An idle reader stops after two minutes;
// OS suspend gaps are excluded. A retry retains the same event and duration.
export function useReadingActivity(context:()=>ReadingContext){
  let timer:ReturnType<typeof setInterval>|undefined,last=performance.now(),interaction=last,seconds=0,busy=false
  let previous=context(),epoch=authGeneration(),owner=accountId()
  let active=appForeground()
  let pending:{body:{bookId:string;branchId:string;revisionId:string;eventId:string;seconds:number};epoch:number;owner:string}|null=null
  const currentIdentity=()=>epoch===authGeneration()&&owner===accountId()
  function interact(){interaction=performance.now()}
  async function flush(){
    if(busy||!currentIdentity())return
    if(!pending&&seconds>=1&&previous.bookId){const duration=Math.min(60,Math.floor(seconds));seconds-=duration;pending={body:{bookId:previous.bookId,branchId:previous.branchId,revisionId:previous.revisionId,eventId:newKey(),seconds:duration},epoch,owner}}
    if(!pending)return
    const request=pending;busy=true
    try{await api('/reading-activity',{method:'POST',body:request.body});if(request===pending&&request.epoch===authGeneration()&&request.owner===accountId())pending=null}catch{/* Keep this event for the next foreground retry; never invent a new ID. */}finally{busy=false}
  }
  function tick(){
    const instant=performance.now(),delta=(instant-last)/1000;last=instant
    if(!currentIdentity()){seconds=0;pending=null;epoch=authGeneration();owner=accountId();previous=context();return}
    if(active&&appForeground()&&previous.enabled&&document.visibilityState==='visible'&&document.hasFocus()&&instant-interaction<120000&&delta<2.5)seconds+=delta
    if(seconds>=30||pending)void flush()
  }
  function hide(){tick();active=false;void flush()}
  function resume(){active=appForeground();last=performance.now();interact();if(pending)void flush()}
  function visibility(){if(document.visibilityState!=='visible')hide();else if(!native)resume()}
  watch(()=>JSON.stringify(context()),()=>{tick();void flush();previous=context();seconds=0;last=performance.now();interact()})
  onMounted(()=>{previous=context();active=appForeground();timer=setInterval(tick,1000);for(const event of ['pointerdown','keydown','scroll'])window.addEventListener(event,interact,{passive:true});document.addEventListener('visibilitychange',visibility);window.addEventListener('reader-save',hide);window.addEventListener('reader-resume',resume)})
  onUnmounted(()=>{hide();clearInterval(timer);for(const event of ['pointerdown','keydown','scroll'])window.removeEventListener(event,interact);document.removeEventListener('visibilitychange',visibility);window.removeEventListener('reader-save',hide);window.removeEventListener('reader-resume',resume)})
}
