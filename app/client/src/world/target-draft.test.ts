import {it,expect} from 'vitest'
import {targetDraft} from './target-draft'
import type {WorldEntity} from './types'
const a={id:'a',name:'梅舟',type:'character',nearby:true} as WorldEntity,b={id:'b',name:'林渡',type:'character',nearby:true} as WorldEntity
it('changes the conversation target without nesting prefills or losing unsent words',()=>{
  const initial=targetDraft('这封信要送给谁？',a,[a,b])
  expect(targetDraft(initial,b,[a,b])).toBe('我对林渡说：“这封信要送给谁？')
})
it('selecting the same person twice keeps one target prefill',()=>expect(targetDraft(targetDraft('',a,[a,b]),a,[a,b])).toBe('我对梅舟说：“'))
it('a remembered remote target can become a nearby conversation after reload',()=>{
  const input=targetDraft('我还想问问旧港。',{...a,nearby:false},[a,b])
  expect(targetDraft(input,b,[a,b])).toBe('我对林渡说：“我还想问问旧港。')
})
it('leaves freely edited drafts and an unrecognized name untouched',()=>expect(targetDraft('我对陌生人说：“你好',b,[a,b])).toBe('我对林渡说：“我对陌生人说：“你好'))
