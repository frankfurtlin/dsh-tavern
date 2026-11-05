import {describe,it,beforeEach,afterEach,expect,vi} from 'vitest'
import type {Router} from 'vue-router'
import {expectsChatContext,hasChatContext,readChatContext,MESSAGE_TEXT_LIMIT,RECENT_MESSAGE_LIMIT} from './dsh-context'
import {
  clearChatContextSnapshot,getChatContextSnapshot,patchChatContextSnapshot,
  setChatContextSnapshot,subscribeChatContext,type ChatContextSnapshot,
} from './tavern/context-snapshot'

function routerFor(path:string):Router{
  return {currentRoute:{value:{path,params:{}}}} as unknown as Router
}

function snapshot(overrides:Partial<ChatContextSnapshot>={}):ChatContextSnapshot{
  return{
    chatId:'c1',
    cardId:'k1',
    characterName:'林渡',
    character:{
      id:'k1',name:'林渡',description:'雨夜书店老板。',personality:'克制而温和',
      scenario:'旧信书店的雨夜',firstMessage:'灯还亮着。',tags:['言情'],hasAvatar:false,
    },
    messages:[{role:'user' as const,text:'我回来了。'},{role:'assistant' as const,text:'伞放门口。'}],
    loaded:true,
    ...overrides,
  }
}

beforeEach(()=>{clearChatContextSnapshot()})
afterEach(()=>{clearChatContextSnapshot()})

describe('chat context availability',()=>{
  it('reports context on the loaded chat page',()=>{
    setChatContextSnapshot(snapshot())
    expect(expectsChatContext(routerFor('/chats/c1'))).toBe(true)
    expect(hasChatContext(routerFor('/chats/c1'))).toBe(true)
  })

  it('has no context on the home, character or unknown pages',()=>{
    setChatContextSnapshot(snapshot())
    for(const path of ['/','/characters/k1','/settings','/chats/c1/messages']){
      expect(expectsChatContext(routerFor(path))).toBe(false)
      expect(hasChatContext(routerFor(path))).toBe(false)
      expect(readChatContext(routerFor(path),'continue')).toBeNull()
    }
  })

  it('waits for the chat snapshot before claiming context',()=>{
    expect(hasChatContext(routerFor('/chats/c1'))).toBe(false)
    expect(readChatContext(routerFor('/chats/c1'),'continue')).toBeNull()
  })

  it('ignores a snapshot that belongs to another chat or is still loading',()=>{
    setChatContextSnapshot(snapshot({chatId:'c2'}))
    expect(hasChatContext(routerFor('/chats/c1'))).toBe(false)
    expect(readChatContext(routerFor('/chats/c1'),'continue')).toBeNull()
    setChatContextSnapshot(snapshot({loaded:false}))
    expect(hasChatContext(routerFor('/chats/c1'))).toBe(false)
  })
})

describe('chat context payload',()=>{
  it('returns the character card fields and recent messages',()=>{
    setChatContextSnapshot(snapshot())
    expect(readChatContext(routerFor('/chats/c1'),'continue')).toEqual({
      appPath:'/chats/c1',
      chatId:'c1',
      cardId:'k1',
      characterName:'林渡',
      characterDescription:'雨夜书店老板。',
      personality:'克制而温和',
      scenario:'旧信书店的雨夜',
      firstMessage:'灯还亮着。',
      recentMessages:[{role:'user',text:'我回来了。'},{role:'assistant',text:'伞放门口。'}],
      activeCharacter:'林渡',
    })
  })

  it('handles a missing card without dropping the chat context',()=>{
    setChatContextSnapshot(snapshot({character:null,characterName:'林渡'}))
    const context=readChatContext(routerFor('/chats/c1'),'roleplay','林渡')
    expect(context).toMatchObject({characterName:'林渡',characterDescription:'',personality:'',scenario:'',firstMessage:'',activeCharacter:'林渡'})
  })

  it('keeps at most the last 12 messages and truncates each to 1000 characters',()=>{
    const long='雨'.repeat(MESSAGE_TEXT_LIMIT+200)
    const messages=Array.from({length:RECENT_MESSAGE_LIMIT+3},(_,index)=>({
      role:(index%2===0?'user':'assistant') as 'user'|'assistant',
      text:index===0?long:'第'+index+'条',
    }))
    setChatContextSnapshot(snapshot({messages}))
    const context=readChatContext(routerFor('/chats/c1'),'continue')!
    expect(context.recentMessages).toHaveLength(RECENT_MESSAGE_LIMIT)
    expect(context.recentMessages[0]).toEqual({role:'assistant',text:'第3条'})
    const truncated=snapshot({messages:[{role:'user',text:long}]})
    setChatContextSnapshot(truncated)
    expect(readChatContext(routerFor('/chats/c2'),'continue')).toBeNull()
    setChatContextSnapshot(snapshot({messages:[{role:'user',text:long}]}))
    const only=readChatContext(routerFor('/chats/c1'),'continue')!
    expect(only.recentMessages[0]!.text).toHaveLength(MESSAGE_TEXT_LIMIT)
  })
})

describe('chat context subscriptions',()=>{
  it('notifies subscribers when the snapshot is published, patched or cleared',()=>{
    const listener=vi.fn()
    const off=subscribeChatContext(listener)
    setChatContextSnapshot(snapshot())
    patchChatContextSnapshot('c1',{character:null})
    clearChatContextSnapshot('c1')
    expect(listener).toHaveBeenCalledTimes(3)
    expect(getChatContextSnapshot()).toBeNull()
    off()
    setChatContextSnapshot(snapshot())
    expect(listener).toHaveBeenCalledTimes(3)
  })

  it('only clears the snapshot of the chat that owns it',()=>{
    setChatContextSnapshot(snapshot({chatId:'c1'}))
    clearChatContextSnapshot('c2')
    expect(getChatContextSnapshot()?.chatId).toBe('c1')
    clearChatContextSnapshot('c1')
    expect(getChatContextSnapshot()).toBeNull()
  })
})
