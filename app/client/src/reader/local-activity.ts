import {ref,watch,onMounted,onUnmounted} from 'vue'
import {onBeforeRouteLeave} from 'vue-router'
import type {LocalVault} from '../repositories/local-vault'
import {ReadingMeter,localDay,type ReadingEvent} from './activity-contract'
interface ReadingContext {vault:LocalVault|null;enabled:boolean;storyId:string;source:ReadingEvent['source']|null;anchor:ReadingEvent['anchor']|null}
/** Only durable local events enter reports; an ACK retry retains its payload. */
export function useLocalReadingActivity(context:()=>ReadingContext){
 const error=ref(''),readerId=crypto.randomUUID();let meter=new ReadingMeter(performance.now()),previous=context(),timer:ReturnType<typeof setInterval>|undefined,work:Promise<void>|null=null,disposed=false;const queue:{vault:LocalVault;id:string;value:ReadingEvent}[]=[]
 const eligible=(c:ReadingContext)=>queue.length<32&&c.enabled&&!!c.vault?.unlocked&&!!c.source&&!!c.anchor&&document.visibilityState==='visible'&&document.hasFocus()
 const identity=(c:ReadingContext)=>JSON.stringify([c.vault?.id,c.storyId,c.source])
 function tick(){const now=performance.now(),current=context();if(identity(current)!==identity(previous)){meter.sample(now,false);void flush();meter=new ReadingMeter(now);previous=current;return}meter.sample(now,eligible(current));previous=current;if(meter.seconds>=15||queue.length)void flush()}
 function interact(){tick();meter.touch(performance.now())}
 async function flush(){
  if(queue.length<32&&previous.vault?.unlocked&&previous.source&&previous.anchor&&meter.seconds){const now=new Date(),seconds=meter.takeSeconds(),id='reading-event:'+crypto.randomUUID();queue.push({vault:previous.vault,id,value:{assetType:'reading-event',storyId:previous.storyId,source:{...previous.source},anchor:{...previous.anchor},seconds,recordedAt:now.toISOString(),localDate:localDay(now),readerId}})}
  if(work)return work
  if(!queue.length)return
  work=(async()=>{while(queue.length){const request=queue[0]!;try{await request.vault.commit(request.id,'settings',request.id,0,request.value);if(queue[0]===request)queue.shift();if(!disposed&&context().vault===request.vault)error.value=''}catch{if(!request.vault.unlocked||context().vault!==request.vault){if(queue[0]===request)queue.shift();continue}try{if(await request.vault.wasDeleted(request.value.storyId)){if(queue[0]===request)queue.shift();if(!disposed)error.value='';continue}}catch{}if(!disposed)error.value='阅读时间尚未保存，可重试';break}}})()
  try{await work}finally{work=null}
 }
 function pause(){tick();meter.reset(performance.now());void flush()}
 function resume(){meter.reset(performance.now());if(queue.length)void flush()}
 function visibility(){if(document.visibilityState==='visible')resume();else pause()}
 function lock(){queue.length=0;meter=new ReadingMeter(performance.now());previous=context();error.value=''}
 watch(()=>identity(context()),()=>{tick();meter.reset(performance.now())})
 onMounted(()=>{previous=context();meter.reset(performance.now());timer=setInterval(tick,1000);for(const event of ['pointerdown','keydown','scroll'])window.addEventListener(event,interact,{passive:true});window.addEventListener('blur',pause);window.addEventListener('focus',resume);window.addEventListener('pagehide',pause);window.addEventListener('reader-local-locked',lock);document.addEventListener('visibilitychange',visibility)})
 onBeforeRouteLeave(async()=>{tick();await flush()})
 onUnmounted(()=>{tick();void flush();disposed=true;clearInterval(timer);for(const event of ['pointerdown','keydown','scroll'])window.removeEventListener(event,interact);window.removeEventListener('blur',pause);window.removeEventListener('focus',resume);window.removeEventListener('pagehide',pause);window.removeEventListener('reader-local-locked',lock);document.removeEventListener('visibilitychange',visibility)})
 return {activityError:error,retryActivity:flush}
}
