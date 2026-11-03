import {it,expect} from 'vitest'
import {appendRelationship,relationshipScore,type Relationship,companionContext,type CompanionProfile,type CompanionMemory} from './contract'
import {chatPrompt} from '../prompt-runtime/chat'
import type {LocalVault} from '../repositories/local-vault'
import {emptyCard} from '../tavern/card-format'
import type {ChatSession,ChatMessage} from '../tavern/chat-contract'
import type {Document} from '../domain/local-contract'
import {validateUpdate} from '../domain/mutation-policy'
const at='2026-10-05T12:00:00.000Z'
it('replaying a real interaction does not double relationship growth and changed event data is rejected',()=>{
  const base:Relationship={companionId:'a',algorithm:'shared-events-v1',events:[],wallet:1000,anniversaries:[]},event={id:'conversation:m1',type:'conversation' as const,at,sourceId:'m1',label:'你好'}
  const once=appendRelationship(base,event),again=appendRelationship(once,event);expect(relationshipScore(again)).toEqual({familiarity:1,sharedDays:1});expect(again.wallet).toBe(1000)
  expect(()=>appendRelationship(again,{...event,label:'已变化'})).toThrow('内容不同')
  expect(relationshipScore(appendRelationship(again,{id:'gift:g1',type:'gift',at,sourceId:'g1',label:'一束花',amount:20})).familiarity).toBe(1)
})
it('only this companion active memories survive a long-history budget and corrected text wins',async()=>{
  const card=emptyCard();card.identityId='stable-a';card.name='同名';const session:ChatSession={title:'同名',cards:[{id:'a-card',revision:1,value:card}],messageIds:[],persona:{name:'我',description:''},mode:'roleplay',authorNote:'',modelOverride:null,archived:false,parent:null,createdAt:at,currentTaskId:null,companionId:'a'}
  const profile:CompanionProfile={name:'同名',identityId:'stable-a',personality:'温柔且简洁',style:'gentle',createdAt:at,user:{name:'阿远',description:'喜欢海边'},modelOverride:null,proactiveEnabled:false}
  const mem=(id:string,companionId:string,text:string,status:CompanionMemory['status']):Document<CompanionMemory>=>({id,kind:'memory',revision:1,updatedAt:at,value:{companionId,text,status,source:{kind:'user',text,at},createdAt:at}})
  const vault={read:async()=>({id:'a',kind:'companion',revision:4,value:profile}),list:async()=>[mem('m','a','更正：喜欢茉莉茶','active'),mem('retired','a','旧事实：喜欢咖啡','retired'),mem('other','b','PRIVATE_OTHER_COMPANION','active')]} as unknown as LocalVault
  const privateContext=await companionContext(vault,session),history:Array<Document<ChatMessage>>=Array.from({length:100},(_,index)=>({id:'row'+index,kind:'message',revision:1,updatedAt:at,value:{sessionId:'s',role:'user',speakerId:null,variants:[{id:'v'+index,text:'近期对话'.repeat(100),state:'complete',taskId:null,createdAt:at}],selectedVariantId:'v'+index,hidden:false,createdAt:at}}))
  const prompt=chatPrompt(session,history,'stable-a',6000,500,privateContext),system=prompt.messages[0]!.content
  expect(system).toContain('更正：喜欢茉莉茶');expect(system).toContain('温柔且简洁');expect(system).not.toContain('旧事实');expect(system).not.toContain('PRIVATE_OTHER');expect(prompt.messages.length).toBeLessThan(history.length)
  await expect(companionContext(vault,{...session,cards:[{...session.cards[0]!,value:{...card,identityId:'stable-b'}}]})).rejects.toThrow('失效')
})
it('runtime create boundaries reject empty stories, invalid candidate selection, and negative narrative balances',()=>{
  expect(()=>validateUpdate('story',null,{})).toThrow('合同无效')
  expect(()=>validateUpdate('message',null,{sessionId:'s',role:'assistant',speakerId:'c',variants:[{id:'v',text:'旧文',state:'complete',createdAt:at}],selectedVariantId:'missing',hidden:false,createdAt:at})).toThrow('variants')
  expect(()=>validateUpdate('relationship',null,{companionId:'a',algorithm:'shared-events-v1',events:[],wallet:-1,anniversaries:[]})).toThrow('合同无效')
})
