import type {Document,ModelSlot,ProviderConfiguration} from '../domain/local-contract'
import {ContractError} from '../domain/local-contract'
import {LocalVault} from './local-vault'
export interface ModelDefaults {slots:Partial<Record<ModelSlot,string>>}
export async function configuredModels(vault:LocalVault){return (await vault.list<ProviderConfiguration>('settings')).filter(row=>row.id.startsWith('model:')&&['chat-completions','responses','anthropic','gemini'].includes(row.value.protocol))}
export async function slotModel(vault:LocalVault,slot:ModelSlot,override?:string):Promise<Document<ProviderConfiguration>>{
  const defaults=await vault.read<ModelDefaults>('settings:models'),id=override||defaults?.value.slots[slot]
  if(!id)throw new ContractError('MODEL_CONFIGURATION','请先在“我的 → 模型”配置'+({text:'文本',vision:'视觉',image:'图像',speech:'语音',video:'视频',embedding:'向量'}[slot])+'模型')
  const model=await vault.read<ProviderConfiguration>(id)
  if(!model||model.kind!=='settings'||!['chat-completions','responses','anthropic','gemini'].includes(model.value.protocol)||!model.value.enabled||!model.value.slots.includes(slot))throw new ContractError('MODEL_CONFIGURATION','所选模型已停用或不支持该用途，请重新选择')
  return model
}
