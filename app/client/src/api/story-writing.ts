import {ApiError} from './index'
import type {Anchor,Branch,Operation,ReviewWarning} from './types'

const reviewMessages:Record<string,string>={
  SPOILER:'可能涉及原作后续情节。',
  HISTORY_CONFLICT:'可能与起点之前的故事事实不同。',
  CHARACTER_KNOWLEDGE:'人物可能使用了原本尚未获知的信息。',
  INJECTION:'部分内容可能偏离故事设定。',
  UNCERTAIN:'AI 未能确定这段内容是否符合原故事设定。',
  CHECK_UNAVAILABLE:'内容检查暂时不可用，生成的正文已保留。',
  WRITING_INTERRUPTED:'后续写作中断，已完成的正文已保留。',
}
export function writingReviewMessages(warnings:ReviewWarning[]=[]):string[] {
  return [...new Set(warnings.map(w=>reviewMessages[w.code]||reviewMessages.UNCERTAIN))]
}

export function writingChapterCount(value:unknown):number|null {
  if(typeof value!=='number'&&typeof value!=='string')return null
  if(typeof value==='string'&&!value.trim())return null
  const count=typeof value==='number'?value:Number(value)
  return Number.isInteger(count)&&count>=1&&count<=10?count:null
}

export function writingOperation(anchor:Anchor,branch:Branch,operation:string,profileId:string,instruction:string,chapterCount:number):Operation {
  const wish=instruction.trim()
  const selected=wish&&['continue_at_tail','continue_from_anchor'].includes(operation)?'counterfactual':operation
  const appending=selected==='continue_at_tail'&&!!branch.parentBranchId
  const name=Array.from(wish.replace(/\s+/g,' ')).slice(0,40).join('')||(operation==='counterfactual'?'另一种剧情':'AI 续写')
  return {operation:selected,profileId,anchor:{...anchor},expectedHeadRevisionId:anchor.revisionId,chapterCount,
    instruction:wish||(operation==='counterfactual'?'从这里发展另一种可能，保持人物性格与世界设定一致。':''),
    target:{mode:appending?'append_existing':'new_child_branch',name:appending?branch.name:name,branchId:branch.id}}
}

const explanations:Record<string,string>={
  NO_PROVIDER:'还没有可用的默认模型，请先到模型设置添加通道。',
  PROFILE_INVALID:'默认模型暂时不可用，请检查模型设置。',
  CONSENT_REQUIRED:'写作准备已过期，请重新开始这次写作。',
  BUDGET_NOT_AUTHORIZED:'已达到你设置的模型用量上限，可以到模型设置查看。',
  CONTEXT_BUDGET_EXCEEDED:'这段故事较长，当前模型暂时无法完成，请换一个起点或检查模型设置。',
  CONCURRENCY_LIMIT:'已有几次写作正在排队，请稍等片刻。',
  BRANCH_ARCHIVED:'这条故事线已归档，请先恢复它再继续写作。',
  REVISION_CONFLICT:'这条故事线已有新内容。你的写作结果已保留，可以从原起点另开一条故事线。',
  ANCHOR_INVALID:'写作起点已变化，请重新选择段落。',
  TASK_CANCELLED:'这次写作已停止，原故事仍然保留。',
  NETWORK_UNAVAILABLE:'网络暂时不可用。已提交的写作会继续保存，恢复网络后可以查看。',
  PROVIDER_STATUS_UNKNOWN:'模型连接中断，本次写作已停止，响应与用量暂时无法确认。原文仍然保留，重新写作会发起新的请求。',
  PROVIDER_CONNECTION_INCOMPLETE:'模型连接中断，请稍后重新查看这次写作。',
  PERSISTENCE_UNAVAILABLE:'写作结果正在恢复保存，请稍后查看。',
  AI_CONTEXT_NOT_READY:'故事背景还在整理，请稍后查看。',
  SESSION_CHANGED:'账号已变化，请重新打开写作面板。',
  GENERATION_SPOILER:'新章节可能提前透露原作后续情节，因此未加入故事线。可以调整发展方向再写。',
  GENERATION_HISTORY_CONFLICT:'新章节可能与起点之前的故事事实冲突，因此未加入故事线。可以调整发展方向再写。',
  GENERATION_CHARACTER_KNOWLEDGE:'新章节中的人物可能使用了尚未获知的信息，因此未加入故事线。可以调整发展方向再写。',
  GENERATION_BLOCKED:'新章节未通过故事内容检查，因此未加入故事线。原文仍然保留，可以调整发展方向再写。',
  GENERATION_INCOMPLETE:'这次没有生成篇幅和结构完整的章节。原文仍然保留，可以重新写一次。',
  VALIDATION_UNCERTAIN:'新章节已生成，但内容检查未能给出可信结果，因此未加入故事线。原文仍然保留，可以重新写一次。',
}

export function writingError(error:unknown):string {
  const code=typeof error==='string'?error:error instanceof ApiError?error.code:''
  if(explanations[code])return explanations[code]
  if(code.startsWith('ANALYSIS_'))return '暂时没能整理好这段故事，原文仍然保留。请稍后重新写作。'
  if(code.startsWith('PROVIDER_'))return '模型暂时没有给出完整结果，请稍后查看或重新写作。'
  if(code.startsWith('GENERATION_')||code.startsWith('VALIDATION_'))return '这次写作没有通过完整性检查，原文仍然保留。可以重新写一次。'
  return '这次操作暂时没能完成，已保存的故事和结果仍然保留。请重试。'
}
