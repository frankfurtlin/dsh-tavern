import {describe,it,expect} from 'vitest'
import {readingAnchorLink,chatStoryLinks} from './reading-links'
import {emptyCard} from '../tavern/card-format'
import type {ChatSession} from '../tavern/chat-contract'
import type {LocalVault} from '../repositories/local-vault'
describe('context-preserving return links',()=>{
 it('preserves branch, exact UTF16 point and historical revision',()=>{expect(readingAnchorLink({storyId:'book',branchId:'line',paragraphId:'p',offset:5,lineRevision:3})).toEqual({path:'/local/read/book',query:{branch:'line',paragraph:'p',offset:'5',revision:'3'}})})
 it('uses the frozen character source for legacy card revisions, not the newer branch HEAD',async()=>{const origin={characterId:'character',characterRevision:2,anchor:{storyId:'book',branchId:'line',paragraphId:'p',offset:5}},card={...emptyCard(),name:'林渡',novelOrigin:origin},calls:unknown[]=[];const vault={read:async(...args:unknown[])=>{calls.push(args);return {kind:'settings',value:{assetType:'novel-character',scope:{storyId:'book',branchId:'line',branchRevision:3,storyRevision:9}}}}} as unknown as LocalVault;const links=await chatStoryLinks(vault,{cards:[{id:'card',revision:1,value:card}]} as ChatSession);expect(calls).toEqual([['character',2]]);expect(links[0]!.to.query.revision).toBe('3')})
 it('does not advertise unavailable or foreign source records',async()=>{const card={...emptyCard(),novelOrigin:{characterId:'character',characterRevision:1,anchor:{storyId:'book',branchId:null,paragraphId:'p',offset:0}}};for(const source of [null,{kind:'card',value:{assetType:'novel-character',scope:{storyId:'book',branchId:null}}},{kind:'settings',value:{assetType:'novel-character',scope:{storyId:'other',branchId:null}}}]){const vault={read:async()=>source} as unknown as LocalVault;expect(await chatStoryLinks(vault,{cards:[{id:'card',revision:1,value:card}]} as ChatSession)).toEqual([])}})
})
