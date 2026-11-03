import {afterEach,describe,expect,it,vi} from 'vitest'
import {SystemSpeech} from './system-speech'
class Utterance {
  onstart:(()=>void)|null=null;onboundary:((event:{charIndex:number})=>void)|null=null;onend:(()=>void)|null=null;onerror:((event:{error:string})=>void)|null=null
  constructor(public text:string){}
}
function environment(){const spoken:Utterance[]=[],cancel=vi.fn(),synthesis={getVoices:()=>[],speak:(utterance:Utterance)=>spoken.push(utterance),cancel,pause:vi.fn(),resume:vi.fn()};vi.stubGlobal('window',{speechSynthesis:synthesis});vi.stubGlobal('speechSynthesis',synthesis);vi.stubGlobal('SpeechSynthesisUtterance',Utterance);return {spoken,cancel}}
afterEach(()=>{vi.useRealTimers();vi.unstubAllGlobals()})
describe('system speech lifecycle and original reading anchors',()=>{
  it('keeps one sleep deadline across consecutive paragraphs and silences stale callbacks after stop',()=>{vi.useFakeTimers();vi.setSystemTime(new Date('2026-10-06T00:00:00Z'));const {spoken,cancel}=environment(),changed=vi.fn();let paragraph=0;const speech=new SystemSpeech(changed,()=>{if(++paragraph===1)speech.speak('第二段',{minutes:1})});speech.speak('第一段',{minutes:1});spoken[0]!.onstart!();vi.advanceTimersByTime(40000);spoken[0]!.onend!();spoken[1]!.onstart!();const stale=spoken[1]!.onboundary!;vi.advanceTimersByTime(20000);expect(cancel).toHaveBeenCalledTimes(1);expect(changed).toHaveBeenLastCalledWith('idle',0);stale({charIndex:2});expect(changed).toHaveBeenLastCalledWith('idle',0);speech.speak('新的听书',{minutes:1});spoken[2]!.onstart!();vi.advanceTimersByTime(59000);expect(cancel).toHaveBeenCalledTimes(1);vi.advanceTimersByTime(1000);expect(cancel).toHaveBeenCalledTimes(2)})
  it('preserves UTF16 boundaries without slicing emoji in half and starts the timer at actual speech start',()=>{vi.useFakeTimers();const {spoken}=environment(),changed=vi.fn(),speech=new SystemSpeech(changed);speech.speak('甲😀乙',{offset:2,minutes:1});expect(spoken[0]!.text).toBe('😀乙');vi.advanceTimersByTime(120000);expect(changed).toHaveBeenLastCalledWith('waiting',1);spoken[0]!.onstart!();spoken[0]!.onboundary!({charIndex:1});expect(changed).toHaveBeenLastCalledWith('speaking',1);spoken[0]!.onboundary!({charIndex:2});expect(changed).toHaveBeenLastCalledWith('speaking',3);speech.stop()})
})
