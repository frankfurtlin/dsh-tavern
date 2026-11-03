import {ContractError,type LocalStory} from '../domain/local-contract'
import type {LocalVault} from '../repositories/local-vault'
import type {LocalBranch} from './contract'
import type {EditSource} from './edits-contract'

/** Scope and lifecycle use current heads; the text source stays pinned. */
export async function editSourceConditions(vault:LocalVault,source:EditSource,pinned=source.policy==='forkPinned'){
 const [story,branch]=await Promise.all([vault.read<LocalStory>(source.storyId),source.kind==='branch'?vault.read<LocalBranch>(source.id):Promise.resolve(null)])
 if(story?.kind!=='story'||source.kind==='story'&&source.id!==story.id||source.kind==='branch'&&(branch?.kind!=='branch'||branch.value.storyId!==story.id))throw new ContractError('WRITING_EDIT_MISSING','选区来源已失效，原候选保留')
 if(branch?.value.archived)throw new ContractError('BRANCH_ARCHIVED','这条故事线已归档，可以阅读；恢复后再修订')
 const head=branch||story
 if(!pinned&&(head.revision!==source.revision||story.revision!==source.storyRevision))throw new ContractError('REVISION_CONFLICT','所选原文已有新版本，当前输入仍保留')
 const [original,originalStory]=await Promise.all([vault.read(source.id,source.revision),vault.read(source.storyId,source.storyRevision)])
 if(original?.kind!==source.kind||originalStory?.kind!=='story')throw new ContractError('WRITING_EDIT_MISSING','选区历史版本缺失，未继续修订')
 return [{id:story.id,kind:'story' as const,revision:story.revision},...(branch?[{id:branch.id,kind:'branch' as const,revision:branch.revision}]:[])]
}
