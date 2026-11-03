import {it,expect} from 'vitest'
import {cardAuthoringResult} from './card-authoring-result'
import {readCard} from '../tavern/card-format'
import {builtinSkill} from './catalog'
import {validateSkillProposals,type SkillRun} from './contract'
it('turns only a complete authored result into a frozen source-bound revision proposal',()=>{const card=readCard('{"name":"陆岚","description":"旧简介","extensions":{"foreign":{"key":"KEEP"}}}'),ref={id:'card',kind:'card' as const,revision:7,value:card as unknown as Record<string,unknown>},result=cardAuthoringResult('card-revise','{"summary":"缩短问候","card":{"first_mes":"晚上好。"}}',[ref]);expect(result.proposals[0]!.id).toBe('card');expect(result.proposals[0]!.expectedRevision).toBe(7);expect(result.proposals[0]!.before).toEqual(card);expect(result.proposals[0]!.after.identityId).toBe(card.identityId);expect(result.proposals[0]!.after.data).toEqual(card.data);expect(result.proposals[0]!.after.firstMessage).toBe('晚上好。');expect(result.links[0]!.path).toBe('/local/characters?card=card')})
it('never converts partial, extra tool fields or invalid editable fields into a saveable card',()=>{for(const raw of ['{"summary":"未完成"','{"summary":"","card":{}}','{"summary":"改身份","card":{"identityId":"bad"}}','{"summary":"执行","card":{"name":"陆岚","description":"新简介","first_mes":"你好"},"tools":[]}'])expect(()=>cardAuthoringResult('card-design',raw,[])).toThrow();expect(()=>cardAuthoringResult('card-revise','{"summary":"修改","card":{"description":"新的简介"}}',[])).toThrow()})
it('rejects forged durable design outputs with zero or multiple cards and oversized generated fields',()=>{
 const output=cardAuthoringResult('card-design','{"summary":"新角色","card":{"name":"陆岚","description":"修书师","first_mes":"晚上好。"}}',[]),run={manifest:builtinSkill('card-design'),references:[],output} as unknown as SkillRun
 expect(()=>validateSkillProposals(run)).not.toThrow()
 expect(()=>validateSkillProposals({...run,output:{...output,proposals:[]}})).toThrow()
 expect(()=>validateSkillProposals({...run,output:{...output,proposals:[...output.proposals,{...output.proposals[0]!,id:'second'}]}})).toThrow()
 for(const patch of [{description:'x'.repeat(30001)},{tags:Array(129).fill('tag')},{name:' '}])expect(()=>validateSkillProposals({...run,output:{...output,proposals:[{...output.proposals[0]!,after:{...output.proposals[0]!.after,...patch}}]}})).toThrow()
})
it('rechecks durable revision field bounds while allowing untouched legacy fields and valid no-op proposals',()=>{
 const card=readCard(JSON.stringify({name:'陆岚',description:'x'.repeat(30001)})),ref={id:'card',kind:'card' as const,revision:7,value:card as unknown as Record<string,unknown>},output=cardAuthoringResult('card-revise','{"summary":"缩短问候","card":{"first_mes":"晚上好。"}}',[ref]),run={manifest:builtinSkill('card-revise'),references:[ref],output} as unknown as SkillRun
 expect(()=>validateSkillProposals(run)).not.toThrow()
 expect(()=>validateSkillProposals({...run,output:{...output,proposals:[{...output.proposals[0]!,after:{...output.proposals[0]!.after,description:'y'.repeat(30001)}}]}})).toThrow()
 expect(()=>validateSkillProposals({...run,output:{...output,proposals:[{...output.proposals[0]!,after:card as unknown as Record<string,unknown>}]}})).not.toThrow()
})
