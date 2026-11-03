import {it,expect} from 'vitest'
import {stageActors,stageSpeaker,type StageCharacter} from './portrait-stage'
const person=(id:string,visibility='visible',art=true)=>({id,name:id,visibility,expressions:art?{neutral:'/api/art/'+id}:{}} as StageCharacter)
const people=[person('tang'),person('xiao'),person('teacher','visible',false)]
it('shows only the selected conversation partner and replaces them on target changes',()=>{
 expect(stageActors(people,'xiao').map(p=>p.id)).toEqual(['xiao'])
 expect(stageActors(people,'tang').map(p=>p.id)).toEqual(['tang'])
 expect(stageActors(people,'xiao').map(p=>p.id)).toEqual(['xiao'])
 expect(stageActors(people,'')).toEqual([])
})
it('does not display departed, player, unknown, or voice-only roles as physical sprites',()=>{
 expect(stageActors([person('spirit','voice'),person('tang')],'spirit')).toEqual([])
 expect(stageActors(people,'departed')).toEqual([])
 expect(stageActors(people,'player')).toEqual([])
})
it('does not substitute another role for a selected teacher without a sprite',()=>expect(stageActors(people,'teacher')).toEqual([]))
it('lights the exact last registered speaker rather than the selected target or a name in the text',()=>{
 expect(stageSpeaker([{kind:'speech',speakerId:'xiao',text:'唐三，你看。'}],people,'tang')).toBe('xiao')
 expect(stageSpeaker([{kind:'narration',text:'小舞看向唐三。'}],people)).toBeNull()
})
it('does not attribute an unknown speaker to the current conversation partner',()=>{
 expect(stageSpeaker([{kind:'speech',speakerId:'missing',text:'小舞说话。'}],people,'tang')).toBeNull()
 expect(stageSpeaker([],people,'tang')).toBe('tang')
})
