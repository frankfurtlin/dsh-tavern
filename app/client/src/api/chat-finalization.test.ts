import {describe,it,expect,vi} from 'vitest'
import {finalizeChatCache} from './chat-finalization'
const receipt={taskId:'saved-task',clearDraft:true,replySaved:true}
describe('confirmed chat finalization',()=>{
  it('retries only local records after storage failure and retains the same terminal task identity',async()=>{
    const write=vi.fn().mockRejectedValueOnce(new Error('storage full')).mockResolvedValue(undefined)
    await expect(finalizeChatCache('session',receipt,()=>'',()=>true,write)).rejects.toThrow('storage full')
    expect(receipt.taskId).toBe('saved-task')
    expect(await finalizeChatCache('session',receipt,()=>'',()=>true,write)).toBe(true)
    expect(write.mock.calls).toEqual([['chat-draft:session',''],['chat-draft:session',''],['chat-pending:session',null]])
  })
  it('preserves a newly composed draft while clearing the old confirmed request receipt',async()=>{
    const write=vi.fn().mockResolvedValue(undefined)
    expect(await finalizeChatCache('session',receipt,()=> '下一条消息',()=>true,write)).toBe(true)
    expect(write.mock.calls).toEqual([['chat-pending:session',null]])
  })
  it('stops between async writes when the session, owner, or pending intent changes',async()=>{
    let current=true
    const write=vi.fn(async()=>{current=false})
    expect(await finalizeChatCache('session',receipt,()=>'',()=>current,write)).toBe(false)
    expect(write).toHaveBeenCalledTimes(1)
    expect(await finalizeChatCache('session',receipt,()=>'',()=>false,write)).toBe(false)
    expect(write).toHaveBeenCalledTimes(1)
  })
})
