export interface ChatIntent{message:string;turnId:string;key:string}
export interface ChatDraft{draft:string;intent:ChatIntent|null}
export function restoreChatDraft(saved:ChatDraft|undefined,messages:{role:string;turnId:string;text:string}[]):ChatDraft{
 const draft=saved?.draft||'',intent=saved?.intent||null
 const accepted=intent&&messages.some(message=>message.role==='user'&&message.turnId===intent.turnId&&message.text.trim()===intent.message.trim())
 if(!accepted)return {draft,intent}
 return {draft:draft.trim()===intent.message.trim()?'':draft,intent:null}
}
