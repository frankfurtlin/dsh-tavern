import {ContractError,unicodeOffset} from '../domain/local-contract'
export type SpeechState='idle'|'waiting'|'speaking'|'paused'|'finished'
/** Browser system speech. Every boundary refers to the original UTF16 text; it never calls a model. */
export class SystemSpeech {
  private utterance:SpeechSynthesisUtterance|null=null;private epoch=0;private start=0;private offset=0;private source='';private deadline=0;private timer:ReturnType<typeof setTimeout>|null=null
  constructor(private changed:(state:SpeechState,offset:number,error?:string)=>void,private finished:()=>void=()=>{}){}
  speak(text:string,options:{voice?:string;rate?:number;offset?:number;minutes?:number}={}){
    this.stop(false);if(!('speechSynthesis' in window)||typeof SpeechSynthesisUtterance==='undefined')throw new ContractError('SPEECH_CAPABILITY','此环境没有系统听书，可选择在线配音或继续阅读')
    if(this.deadline&&Date.now()>=this.deadline){this.stop();return}
    const ticket=++this.epoch;this.source=text;this.offset=unicodeOffset(text,options.offset||0);this.start=this.offset
    if(this.offset>=text.length){this.changed('finished',text.length);this.finished();return}
    const utterance=new SpeechSynthesisUtterance(text.slice(this.offset));this.utterance=utterance;utterance.rate=Math.max(.5,Math.min(2,options.rate||1));utterance.lang='zh-CN';utterance.voice=speechSynthesis.getVoices().find(voice=>voice.voiceURI===options.voice)||null
    utterance.onstart=()=>{if(ticket!==this.epoch)return;if(!this.deadline&&options.minutes)this.deadline=Date.now()+options.minutes*60000;this.changed('speaking',this.offset);if(this.deadline)this.timer=setTimeout(()=>this.stop(),Math.max(0,this.deadline-Date.now()))}
    utterance.onboundary=event=>{if(ticket!==this.epoch)return;this.offset=unicodeOffset(this.source,this.start+event.charIndex);this.changed('speaking',this.offset)}
    utterance.onend=()=>{if(ticket!==this.epoch)return;this.offset=this.source.length;this.utterance=null;if(this.timer)clearTimeout(this.timer);this.timer=null;this.changed('finished',this.offset);this.finished()}
    utterance.onerror=event=>{if(ticket!==this.epoch)return;this.utterance=null;if(this.timer)clearTimeout(this.timer);this.timer=null;this.deadline=0;this.changed('idle',this.offset,'系统语音未完成：'+event.error)}
    this.changed('waiting',this.offset);speechSynthesis.speak(utterance)
  }
  pause(){if(!this.utterance)return;speechSynthesis.pause();this.changed('paused',this.offset)}
  resume(){if(!this.utterance)return;speechSynthesis.resume();this.changed('speaking',this.offset)}
  stop(resetDeadline=true){this.epoch++;if(resetDeadline)this.deadline=0;if(this.timer)clearTimeout(this.timer);this.timer=null;if(this.utterance){this.utterance.onend=null;this.utterance.onboundary=null;this.utterance.onstart=null;this.utterance.onerror=null;this.utterance=null;if('speechSynthesis' in window)speechSynthesis.cancel()}this.changed('idle',this.offset)}
}
