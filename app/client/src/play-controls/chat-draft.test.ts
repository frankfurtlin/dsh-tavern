import {it,expect} from 'vitest'
import {restoreChatDraft} from './chat-draft'
const intent={message:'你好',turnId:'own-turn',key:'transport-key'}
it('preserves an unsent local draft while another device has a pending reply',()=>{
 expect(restoreChatDraft({draft:'尚未发送的新想法',intent:null},[{role:'user',turnId:'other-device-turn',text:'来自另一设备'}])).toEqual({draft:'尚未发送的新想法',intent:null})
})
it('reconciles an accepted turn even when it finished before this page reopened',()=>{
 expect(restoreChatDraft({draft:' 你好 ',intent},[{role:'user',turnId:'own-turn',text:'你好'},{role:'assistant',turnId:'own-turn',text:'很高兴见到你'}])).toEqual({draft:'',intent:null})
})
it('keeps the stable intent when acceptance cannot yet be proved',()=>{
 expect(restoreChatDraft({draft:'你好',intent},[{role:'user',turnId:'another-turn',text:'你好'}])).toEqual({draft:'你好',intent})
})
it('does not mistake an assistant quotation or different text for an accepted user turn',()=>{
 expect(restoreChatDraft({draft:'你好',intent},[{role:'assistant',turnId:'own-turn',text:'你好'},{role:'user',turnId:'own-turn',text:'其他内容'}])).toEqual({draft:'你好',intent})
})
it('preserves newer unsent text after reconciling an earlier accepted intent',()=>{
 expect(restoreChatDraft({draft:'我后来写的新内容',intent},[{role:'user',turnId:'own-turn',text:'你好'}])).toEqual({draft:'我后来写的新内容',intent:null})
})
