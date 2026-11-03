import {describe,it,expect} from 'vitest'
import {pendingChatPreview,mergeChatPreview,chatRows,chatRowNote,chatFollowingScroll,confirmedChatDraft,chatAccessDenied,chatAccessMessage} from './chat-stream'
import type {Task,Message,ChatReplyStream} from './types'

const snapshot=(stream:Partial<ChatReplyStream>,id='task')=>({id,replyStream:{sessionId:'session',turnId:'turn',userMessage:'你好',text:'真正的文字',version:1,status:'streaming',...stream}} as Task)
const message=(role:string,text:string,status='saved')=>({id:role,turnId:'turn',taskId:'task',role,text,status,sequence:role==='user'?1:2} as Message)
describe('persisted chat stream projection',()=>{
  it('shows a durable optimistic user immediately and only displays actual snapshot reply text',()=>{
    const initial=pendingChatPreview('session','turn','你好')
    expect(chatRows('session',[],initial).map(row=>[row.role,row.text])).toEqual([['user','你好']])
    const first=mergeChatPreview(initial,snapshot({text:'第一个字'}),'session')!
    expect(chatRows('session',[],first).map(row=>[row.role,row.text])).toEqual([['user','你好'],['assistant','第一个字']])
  })
  it('rejects stale versions and wrong session, turn, task or malformed version',()=>{
    const current=mergeChatPreview(null,snapshot({version:4}),'session')!
    for(const wrong of [snapshot({version:3,text:'旧'}),snapshot({version:4,text:'同序旧'}),snapshot({sessionId:'other',version:5}),snapshot({turnId:'other',version:5}),snapshot({version:5},'other-task'),snapshot({version:NaN})])expect(mergeChatPreview(current,wrong,'session')).toBe(current)
    expect(mergeChatPreview(current,snapshot({version:5,text:'恢复后的全部文字'}),'session')?.text).toBe('恢复后的全部文字')
  })
  it('deduplicates canonical user/assistant against the same request and prefers canonical final text',()=>{
    const preview=mergeChatPreview(null,snapshot({text:'预览文本'}),'session')!
    const rows=chatRows('session',[message('user','你好'),message('assistant','最终正文')],preview)
    expect(rows).toHaveLength(2);expect(rows[1].text).toBe('最终正文');expect(rows.every(row=>!row.preview)).toBe(true)
    expect(chatRows('other',[],preview)).toEqual([])
  })
  it('preserves stopped/interrupted partial words and expresses advisory review notes',()=>{
    for(const status of ['stopped','interrupted'] as const){
      const preview=mergeChatPreview(null,snapshot({status,text:'已经收到的半句话',reviewWarnings:['CANON_CONTRADICTION']}),'session')!
      const row=chatRows('session',[message('user','你好')],preview)[1]
      expect(row.text).toBe('已经收到的半句话');expect(chatRowNote(row)).toContain('已有文字已保留');expect(chatRowNote(row)).toContain('可能与故事有出入')
    }
  })
  it('keeps messages from other turns while merging only the current reply',()=>{
    const old={...message('assistant','上一条消息'),id:'old',turnId:'old-turn',taskId:'old-task'}
    const rows=chatRows('session',[old,message('user','你好')],mergeChatPreview(null,snapshot({}),'session'))
    expect(rows.map(row=>row.text)).toEqual(['上一条消息','你好','真正的文字'])
  })
  it('pauses on upward reading and resumes only when scrolling back to the bottom',()=>{
    expect(chatFollowingScroll(900,750,1300,400,true)).toBe(false)
    expect(chatFollowingScroll(750,760,1500,400,false)).toBe(false)
    expect(chatFollowingScroll(760,1090,1500,400,false)).toBe(true)
    expect(chatFollowingScroll(1090,1090,1700,400,true)).toBe(true)
  })
  it('clears only the confirmed submitted draft and preserves a new draft typed during the reply',()=>{
    expect(confirmedChatDraft('  你好  ','你好','  你好  ')).toBe('')
    expect(confirmedChatDraft('下一条新消息','你好','你好')).toBe('下一条新消息')
    expect(confirmedChatDraft('你好','你好','你好',true)).toBe('你好')
    expect(confirmedChatDraft('你好','你好')).toBe('')
  })
  it('treats authoritative scope/account rejection as revocation rather than retryable transport loss',()=>{
    for(const code of ['ROLE_SCOPE_CHANGED','ROLE_CHARACTER_CHANGED','ROLE_UNAVAILABLE','SESSION_CHANGED','AUTH_REQUIRED','RESOURCE_NOT_FOUND','ANCHOR_INVALID'])expect(chatAccessDenied({code})).toBe(true)
    expect(chatAccessDenied({code:'HTTP_ERROR',status:403})).toBe(true)
    expect(chatAccessMessage({code:'ROLE_SCOPE_CHANGED'})).toContain('重新选择角色')
    expect(chatAccessMessage({code:'SESSION_CHANGED'})).toContain('重新登录')
  })
  it('keeps transient network and budget failures distinct from privacy revocation',()=>{
    for(const error of [new Error('socket closed'),{code:'NETWORK_UNAVAILABLE',status:0},{code:'SERVICE_UNAVAILABLE',status:503},{code:'BUDGET_NOT_AUTHORIZED',status:403}])expect(chatAccessDenied(error)).toBe(false)
  })
})
