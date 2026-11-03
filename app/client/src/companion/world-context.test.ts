import {it,expect} from 'vitest'
import {validateCompanionWorld,companionWorldContext,worldDraft,type CompanionWorld} from './world-context'
import {companionContext,type CompanionProfile} from './contract'
import {validateLocalValue} from '../domain/local-validation'
import type {LocalVault} from '../repositories/local-vault'
import type {ChatSession} from '../tavern/chat-contract'
import {emptyCard} from '../tavern/card-format'
const at='2026-10-06T12:00:00.000Z',world:CompanionWorld={schema:'companion-world-v1',name:'自编北岸',description:'日落后点灯',currentPlace:'纸船书屋',dayPlan:'早上看书',people:[{name:'书店老板',description:'只知道用户在此写下的设定'}],shareWithChat:false,updatedAt:at}
const profile:CompanionProfile={name:'许知',identityId:'own-stable-a',personality:'自编人物',style:'gentle',createdAt:at,user:{name:'我',description:''},modelOverride:null,proactiveEnabled:false}
it('old companion documents still validate, malformed optional worlds cannot enter the vault',()=>{
  expect(()=>validateLocalValue('companion',profile)).not.toThrow()
  expect(()=>validateLocalValue('companion',{...profile,world})).not.toThrow()
  for(const value of [{...world,shareWithChat:'false'},{...world,people:Array(11).fill(world.people[0])},{...world,people:[{name:' ',description:''}]},{...world,updatedAt:'yesterday'},{...world,description:'x'.repeat(6001)},{...world,currentPlace:'x'.repeat(121)}])expect(()=>validateLocalValue('companion',{...profile,world:value})).toThrow('世界设置无效')
})
it('world sharing is off by default and never presented as actual location or shared memories',()=>{
  expect(worldDraft().shareWithChat).toBe(false);expect(companionWorldContext()).toBe(null);expect(companionWorldContext(world)).toBe(null)
  const text=companionWorldContext({...world,shareWithChat:true})!;expect(text).toContain('不是实时位置');expect(text).toContain('不得据此编造共同经历');expect(text).toContain('纸船书屋');expect(text).not.toContain(at)
})
it('editor copy cannot mutate a stored NPC source through nested array aliasing',()=>{
  const draft=worldDraft(world);draft.people[0]!.name='修改的名字';draft.people.push({name:'另一人',description:''});expect(world.people).toHaveLength(1);expect(world.people[0]!.name).toBe('书店老板')
})
it('only the matching companion revision contributes its explicitly enabled world to a request',async()=>{
  const card=emptyCard();card.identityId=profile.identityId
  const session:ChatSession={title:profile.name,cards:[{id:'own-card',revision:1,value:card}],messageIds:[],persona:{name:'我',description:''},mode:'roleplay',authorNote:'',modelOverride:null,archived:false,parent:null,createdAt:at,currentTaskId:null,companionId:'own-a'}
  const vault={read:async()=>({id:'own-a',kind:'companion',revision:9,value:{...profile,world:{...world,shareWithChat:true}}}),list:async()=>[]} as unknown as LocalVault
  const contexts=await companionContext(vault,session);expect(contexts.find(row=>row.id==='companion-world:own-a:9')?.text).toContain('纸船书屋')
  expect(await companionContext(vault,{...session,companionId:undefined})).toEqual([])
  await expect(companionContext(vault,{...session,cards:[session.cards[0]!,session.cards[0]!]})).rejects.toThrow('独立会话')
  await expect(companionContext(vault,{...session,cards:[{...session.cards[0]!,value:{...card,identityId:'other-person'}}]})).rejects.toThrow('失效')
})
