import {ContractError} from '../domain/local-contract'
import {LocalVault} from '../repositories/local-vault'
import {validateAssetLink,type AssetLink,type MediaJob} from './contract'
export interface MediaCompose {assetType:'media-compose';type:MediaJob['type'];prompt:string;source:AssetLink|null;referenceId:string|null;createdAt:string}
/** Private prose belongs in the encrypted vault, never a route query or browser history. */
export async function composeMedia(vault:LocalVault,options:{type?:MediaJob['type'];prompt?:string;source?:AssetLink|null;referenceId?:string;signal?:AbortSignal}){
  options.signal?.throwIfAborted()
  const id='media-compose:'+crypto.randomUUID(),value:MediaCompose={assetType:'media-compose',type:options.type||'image',prompt:(options.prompt||'').slice(0,options.type==='tts'?4096:20000),source:options.source||null,referenceId:options.referenceId||null,createdAt:new Date().toISOString()}
  if(value.source)validateAssetLink(value.source)
  await vault.commitBundle(crypto.randomUUID(),[{kind:'settings',id,expectedRevision:0,value}],options.signal)
  if(!vault.unlocked)throw new ContractError('VAULT_LOCKED','私人库已锁定')
  return {path:'/local/gallery',query:{draft:id}}
}
