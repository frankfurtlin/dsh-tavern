import type {DeletionReceipt} from './deletion-receipt'

export type ReceiptStatus='saved'|'local'|'failed'
type Completion={
  sameAccount:()=>boolean
  saveReceipt:(receipt:DeletionReceipt)=>Promise<{localSaved:boolean;nativeSaved:boolean}>
  clearPrivate:()=>Promise<void>
  native:boolean
}

// Once the server accepts deletion, account cleanup outlives the settings page.
// Save starts synchronously so the receipt is available before identity removal;
// native IO must not delay revocation or make a different account get cleared.
export async function completeAccountDeletion(receipt:DeletionReceipt,completion:Completion){
  if(!completion.sameAccount())return{cleared:false,receiptStatus:'failed' as ReceiptStatus}
  let saved:Promise<ReceiptStatus>
  try{
    saved=completion.saveReceipt(receipt).then(result=>completion.native&&!result.nativeSaved?'local' as const:'saved' as const,()=>'failed' as const)
  }catch{saved=Promise.resolve('failed')}
  if(!completion.sameAccount())return{cleared:false,receiptStatus:await saved}
  const clearing=completion.clearPrivate().then(()=>false,()=>true)
  const [receiptStatus,cleanupFailed]=await Promise.all([saved,clearing])
  return{cleared:true,receiptStatus,cleanupFailed}
}
