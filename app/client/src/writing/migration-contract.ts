import type {WritingRun} from './contract'
/** Only backup-chain verification uses original phases. Live historical edits stay frozen. */
export function originalWritingPhases(run:WritingRun):WritingRun{
 const {originalStatus,...value}=run
 if(!run.historical||originalStatus===undefined)return run
 return {...value,historical:false,status:originalStatus,chapters:run.chapters.map(c=>{const {originalStatus:phase,...chapter}=c;return {...chapter,status:phase??(originalStatus==='running'&&['unknown','partial'].includes(c.status)?'running':c.status)}})}
}
