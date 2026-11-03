import {ContractError,type DomainKind} from '../domain/local-contract'

export function requestedLineRevision(value:unknown):number|undefined {
 if(value===undefined)return undefined
 if(typeof value!=='string'||!/^[1-9][0-9]{0,14}$/.test(value)||!Number.isSafeInteger(Number(value)))throw new ContractError('LINE_REVISION','历史版本编号无效，请重新选择版本')
 return Number(value)
}
export function historyRevisionPage(head:number,page=0,size=20):number[] {
 if(!Number.isSafeInteger(head)||head<1||!Number.isSafeInteger(page)||page<0||!Number.isSafeInteger(size)||size<1||size>50)throw new ContractError('LINE_REVISION','历史分页无效')
 const high=head-page*size
 return high<1?[]:Array.from({length:Math.min(size,high)},(_,i)=>high-i)
}
export function readingRevisionTarget(storyId:string,branchId:string|null,revision:number,paragraphId?:string,offset=0){
 if(!Number.isSafeInteger(revision)||revision<1||!Number.isSafeInteger(offset)||offset<0)throw new ContractError('LINE_REVISION','历史阅读位置无效')
 return {path:'/local/read/'+storyId,query:{branch:branchId||undefined,revision:String(revision),paragraph:paragraphId,offset:paragraphId?String(offset):undefined}}
}
export function revisionProgressKey(base:string,revision?:number){
 if(revision!==undefined&&(!Number.isSafeInteger(revision)||revision<1))throw new ContractError('LINE_REVISION','历史阅读位置版本无效')
 return base+(revision===undefined?'':':revision:'+revision)
}
export function historicalReadingAnchor(anchor:{storyId:string;branchId:string|null;paragraphId:string;offset:number;lineRevision?:number},lookup:(id:string,revision?:number)=>{kind:DomainKind;value:unknown;revision?:number}|undefined){
 const sourceId=anchor.branchId||anchor.storyId,kind=anchor.branchId?'branch':'story',row=lookup(sourceId,anchor.lineRevision)
 if(!row||row.kind!==kind||anchor.lineRevision!==undefined&&row.revision!==anchor.lineRevision)throw new ContractError('LINE_REVISION','阅读位置的历史版本缺失')
 const value=row.value as {storyId?:string;paragraphIds:string[]}
 if(anchor.branchId&&value.storyId!==anchor.storyId||!value.paragraphIds.includes(anchor.paragraphId))throw new ContractError('LINE_REVISION','阅读位置不属于指定历史版本')
 return value
}
