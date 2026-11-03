export interface BookmarkDeletionReceipt {
  deleted:true;bookmarkId:string;bookId:string;version:number;receiptId:string;deletedAt:string
}
export type BookmarkDeletionLookup={found:false}|{found:true;receipt:BookmarkDeletionReceipt}
export interface BookmarkDeletionRequest {bookId:string;bookmarkId:string;expectedVersion:number;key:string;attempted:boolean}
export function validBookmarkReceipt(value:unknown,bookId:string,bookmarkId:string):value is BookmarkDeletionReceipt {
  if(!value||typeof value!=='object')return false
  const receipt=value as Partial<BookmarkDeletionReceipt>
  return receipt.deleted===true&&receipt.bookId===bookId&&receipt.bookmarkId===bookmarkId&&Number.isInteger(receipt.version)&&Number(receipt.version)>=1&&typeof receipt.receiptId==='string'&&!!receipt.receiptId&&typeof receipt.deletedAt==='string'&&Number.isFinite(Date.parse(receipt.deletedAt))
}
