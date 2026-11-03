import {ContractError,type DomainKind,type LocalStory,type Document} from '../domain/local-contract'
import type {LocalBranch} from './contract'
import type {LocalVault} from '../repositories/local-vault'

export interface MainReadingLine {assetType:'main-reading-line';storyId:string;branchId:string|null}
export const mainLineKey=(storyId:string)=>'settings:main-reading-line:'+storyId
export function validateMainReadingLine(value:unknown,id?:string):asserts value is MainReadingLine {
 const v=value as MainReadingLine
 if(!v||typeof v!=='object'||Array.isArray(v)||Object.keys(v).sort().join(' ')!=='assetType branchId storyId'||v.assetType!=='main-reading-line'||typeof v.storyId!=='string'||!v.storyId||v.storyId.length>160||v.branchId!==null&&(typeof v.branchId!=='string'||!v.branchId||v.branchId.length>160)||id!==undefined&&id!==mainLineKey(v.storyId))throw new ContractError('MAIN_READING_LINE','主要阅读线设置无效，原文仍保留')
}
export function mainLineReferences(v:MainReadingLine):{id:string;kind:DomainKind}[]{return [{id:v.storyId,kind:'story'},...(v.branchId?[{id:v.branchId,kind:'branch' as const}]:[])]}
export function validateMainLineRelations(v:MainReadingLine,lookup:(id:string)=>{kind:DomainKind;value:unknown}|undefined){
 if(lookup(v.storyId)?.kind!=='story')throw new ContractError('MAIN_READING_LINE','主要阅读线所属书籍不存在')
 if(v.branchId){const b=lookup(v.branchId);if(b?.kind!=='branch'||(b.value as LocalBranch).storyId!==v.storyId)throw new ContractError('MAIN_READING_LINE','主要阅读线必须属于同一本书')}
}
/** Compare-and-swap only the preference. All source paragraphs and revisions remain untouched. */
export async function setMainReadingLine(vault:LocalVault,storyId:string,branchId:string|null,expectedRevision:number){
 const id=mainLineKey(storyId),before=await vault.read<MainReadingLine>(id)
 if(before?.value.branchId===branchId)return before
 if((before?.revision||0)!==expectedRevision)throw new ContractError('REVISION_CONFLICT','另一窗口已设置主要阅读线，请先刷新')
 const story=await vault.read<LocalStory>(storyId),branch=branchId?await vault.read<LocalBranch>(branchId):null
 if(story?.kind!=='story'||branchId&&(branch?.kind!=='branch'||branch.value.storyId!==storyId))throw new ContractError('MAIN_READING_LINE','找不到这条故事线，原文仍保留')
 const value:MainReadingLine={assetType:'main-reading-line',storyId,branchId}
 // A deleted derived preference starts a new lifecycle at revision zero. A
 // fresh user intent must not collide with an earlier lifecycle's receipt.
 // Retrying after a committed ACK loss returns the matching live head above;
 // an intervening different choice still fails the expected-revision CAS.
 await vault.commitBundle('main-reading-line:'+storyId+':'+crypto.randomUUID(),[{kind:'settings',id,expectedRevision,value}],undefined,[{id:storyId,kind:'story',revision:story.revision},...(branch?[{id:branch.id,kind:'branch' as const,revision:branch.revision}]:[])])
 return (await vault.read<MainReadingLine>(id))!
}
export function primaryLibraryLinks(rows:Document<MainReadingLine>[],branches:Document<LocalBranch>[]){
 const available=new Map(branches.map(b=>[b.id,b.value.storyId])),links=new Map<string,string>()
 for(const row of rows)if(row.value.branchId&&available.get(row.value.branchId)===row.value.storyId)links.set(row.value.storyId,row.value.branchId)
 return links
}
