import {ContractError,type DomainKind,type LocalStory} from '../domain/local-contract'
import {visibleCharacter,type NovelCharacter} from '../novel-characters/contract'
import type {LocalBranch,WritingRun} from './contract'
import {replayAcceptedBasis} from './accepted-basis'
type Lookup=(id:string,revision?:number)=>{kind:DomainKind;value:unknown;revision?:number}|undefined
export function writingEventRelations(run:WritingRun,lookup:Lookup){
 if(!run.eventSchema)return
 if(run.acceptedBasis)replayAcceptedBasis(run,lookup)
 const source=lookup(run.parentBranchId||run.storyId,run.parentRevision),line=source?.value as LocalStory|LocalBranch|undefined
 for(const s of run.eventSubjects||[]){const character=lookup(s.id,s.revision),v=character?.value as NovelCharacter|undefined
  const inherited=run.acceptedBasis?.subjects.some(b=>b.subjectId===s.id&&b.subjectRevision===s.revision&&b.name===s.name)
  if(!line||character?.kind!=='settings'||character.revision!==s.revision||v?.assetType!=='novel-character'||v.name!==s.name||!visibleCharacter(v,run.anchor,line as LocalStory)&&!inherited)throw new ContractError('WRITING_EVENTS','人物事件引用不属于本次已读范围，原正文保留')
 }
}
