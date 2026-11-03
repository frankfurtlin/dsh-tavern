export interface ChatCleanupReceipt {taskId:string;clearDraft:boolean;replySaved:boolean}

// The server task is already terminal. This function performs only local
// bookkeeping; the caller has released its in-memory pending turn first.
export async function finalizeChatCache(sessionId:string,receipt:ChatCleanupReceipt,input:()=>string,current:()=>boolean,write:(key:string,value:unknown)=>Promise<void>):Promise<boolean>{
  if(!current())return false
  if(receipt.clearDraft&&!input()){
    await write('chat-draft:'+sessionId,'')
    if(!current())return false
  }
  await write('chat-pending:'+sessionId,null)
  return current()
}
