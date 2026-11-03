import {canonical,ContractError,unicodeOffset,type DomainKind,type LocalStory,type Paragraph,type ProgressAnchor} from '../domain/local-contract'
import type {CharacterCard} from '../tavern/card-format'
import {validateIndexBinding,type CharacterIndexBinding} from './index-contract'
export interface CharacterSource {id:string;revision:number;inputId:string;limit:number;text:string;start?:number;sliceHash?:string}
export interface CharacterScope {schema?:'novel-character-scope-v2';storyId:string;storyRevision:number;branchId:string|null;branchRevision:number|null;anchor:ProgressAnchor;sources:CharacterSource[]}
export interface CharacterEvidence {id:string;revision:number;offset:number;quote:string}
export type CharacterConfidence='explicit'|'inferred'
export interface CharacterFact {kind:'identity'|'life'|'appearance'|'personality'|'scenario';text:string;confidence:CharacterConfidence;evidence:CharacterEvidence[]}
export interface CharacterAlias {name:string;confidence:CharacterConfidence;evidence:CharacterEvidence[]}
export interface NovelCharacter {assetType:'novel-character';identityId:string;cardId:string;scanId:string;scope:CharacterScope;primary:CharacterEvidence;name:string;aliases:CharacterAlias[];facts:CharacterFact[];firstMessage:string;createdAt:string}
export interface CharacterScan {assetType:'novel-character-scan';scope:CharacterScope;runId:string;characterIds:string[];applied:boolean;historical:boolean;error:string|null;createdAt:string;index?:CharacterIndexBinding}
export interface NovelCardOrigin {characterId:string;characterRevision:number;anchor:ProgressAnchor;versions?:{characterId:string;characterRevision:number}[]}
const record=(v:unknown):v is Record<string,unknown>=>!!v&&typeof v==='object'&&!Array.isArray(v)
const text=(v:unknown,max=160):v is string=>typeof v==='string'&&!!v&&v.length<=max
const integer=(v:unknown,min=0):v is number=>Number.isSafeInteger(v)&&Number(v)>=min
function fail(message:string):never{throw new ContractError('CHARACTER_CONTRACT',message+'；原文和原聊天保留')}
function evidence(value:unknown):asserts value is CharacterEvidence {if(!record(value)||!text(value.id)||!integer(value.revision,1)||!integer(value.offset)||!text(value.quote,8000))fail('人物证据无效')}
function confidence(value:unknown){return value==='explicit'||value==='inferred'}
export function validateCharacterScope(value:unknown):asserts value is CharacterScope {
 if(!record(value)||!text(value.storyId)||!integer(value.storyRevision,1)||value.branchId!==null&&!text(value.branchId)||(value.branchId===null?value.branchRevision!==null:!integer(value.branchRevision,1)))fail('人物来源范围无效')
 const v=value as unknown as CharacterScope,a=v.anchor,v2=v.schema==='novel-character-scope-v2'
 if(v.schema!==undefined&&!v2)fail('人物范围版本无效')
 if(!record(a)||a.storyId!==v.storyId||a.branchId!==v.branchId||!text(a.paragraphId)||!integer(a.offset)||!Array.isArray(v.sources)||!v.sources.length||v.sources.length>32||!v2&&new Set(v.sources.map(s=>s.id)).size!==v.sources.length||new Set(v.sources.map(s=>s.inputId)).size!==v.sources.length)fail('人物范围或起点无效')
 for(const source of v.sources){if(!record(source)||!text(source.id)||!integer(source.revision,1)||!text(source.inputId)||!integer(source.limit,1)||typeof source.text!=='string'||(v2?(!integer(source.start)||source.start!>=source.limit||!text(source.sliceHash,64)||!/^[a-f0-9]{64}$/.test(source.sliceHash!)||source.text.length!==source.limit-source.start!):(source.start!==undefined||source.sliceHash!==undefined||source.text.length!==source.limit)))fail('人物原文副本无效')}
 if(v.sources.reduce((n,s)=>n+s.text.length,0)>64000||new TextEncoder().encode(canonical(v)).byteLength>512*1024)fail('人物原文范围超过上限')
}
export function validateNovelCharacter(value:unknown):asserts value is NovelCharacter {
 if(!record(value)||value.assetType!=='novel-character'||!text(value.identityId)||!text(value.cardId)||!text(value.scanId)||!text(value.name,200)||!Array.isArray(value.aliases)||value.aliases.length>20||!Array.isArray(value.facts)||value.facts.length>40||typeof value.firstMessage!=='string'||value.firstMessage.length>8000||!text(value.createdAt,100))fail('小说人物无效')
 validateCharacterScope(value.scope);evidence(value.primary)
 if(value.primary.quote!==value.name)fail('人物身份需要准确的姓名锚点')
 for(const alias of value.aliases){if(!record(alias)||!text(alias.name,200)||!confidence(alias.confidence)||!Array.isArray(alias.evidence)||!alias.evidence.length||alias.evidence.length>8)fail('别名证据无效');alias.evidence.forEach(evidence)}
 for(const fact of value.facts){if(!record(fact)||!['identity','life','appearance','personality','scenario'].includes(String(fact.kind))||!text(fact.text,4000)||!confidence(fact.confidence)||!Array.isArray(fact.evidence)||!fact.evidence.length||fact.evidence.length>8)fail('人物事实无效');fact.evidence.forEach(evidence)}
}
export function validateCharacterScan(value:unknown):asserts value is CharacterScan {
 if(!record(value)||value.assetType!=='novel-character-scan'||!text(value.runId)||!Array.isArray(value.characterIds)||value.characterIds.length>20||value.characterIds.some(id=>!text(id))||new Set(value.characterIds).size!==value.characterIds.length||typeof value.applied!=='boolean'||typeof value.historical!=='boolean'||value.error!==null&&!text(value.error,2000)||!text(value.createdAt,100))fail('人物整理记录无效')
 validateCharacterScope(value.scope)
 if(value.index!==undefined)validateIndexBinding(value.index)
}
export function validateNovelOrigin(value:unknown):asserts value is NovelCardOrigin {
 if(!record(value)||!text(value.characterId)||!integer(value.characterRevision,1)||!record(value.anchor)||!text(value.anchor.storyId)||value.anchor.branchId!==null&&!text(value.anchor.branchId)||!text(value.anchor.paragraphId)||!integer(value.anchor.offset))fail('人物卡来源无效')
 if(value.versions!==undefined&&(!Array.isArray(value.versions)||!value.versions.length||value.versions.length>2000||value.versions.some((r,i)=>!record(r)||r.characterId!==value.characterId||r.characterRevision!==i+1)||value.versions.length!==value.characterRevision))fail('人物卡历史来源不完整')
}
/** Only vault-owned origins make this extension a typed relation. Portable foreign extensions remain opaque. */
export function validateNovelCardMetadata(card:Record<string,unknown>){
 if(card.novelOrigin===undefined)return
 validateNovelOrigin(card.novelOrigin)
 const check=(holder:unknown,required:boolean)=>{const extensions=record(holder)&&record(holder.extensions)?holder.extensions:null,meta=extensions?.readerNovel
  if(meta===undefined&&!required)return
  const origin=card.novelOrigin as NovelCardOrigin,v2=origin.versions!==undefined
  if(!record(meta)||meta.format!==(v2?'reader-novel-character-v2':'reader-novel-character-v1')||canonical({characterId:meta.characterId,characterRevision:meta.characterRevision,anchor:meta.anchor,...(v2?{versions:meta.versions}:{})})!==canonical(origin)||!Array.isArray(meta.aliases)||meta.aliases.length>(v2?1000:20)||!Array.isArray(meta.facts)||meta.facts.length>(v2?2000:40)||new TextEncoder().encode(canonical(meta)).byteLength>2_000_000)fail('人物卡证据元数据无效')
  for(const alias of meta.aliases){if(!record(alias)||!text(alias.name,200)||!confidence(alias.confidence)||!Array.isArray(alias.evidence)||!alias.evidence.length||alias.evidence.length>8)fail('人物卡别名证据无效');alias.evidence.forEach(evidence)}
  for(const fact of meta.facts){if(!record(fact)||!['identity','life','appearance','personality','scenario'].includes(String(fact.kind))||!text(fact.text,4000)||!confidence(fact.confidence)||!Array.isArray(fact.evidence)||!fact.evidence.length||fact.evidence.length>8)fail('人物卡事实证据无效');fact.evidence.forEach(evidence)}
 }
 check(card.data,true);if(record(card.source))check(card.source.data,false)
}
export function characterReferences(scope:CharacterScope):{id:string;kind:DomainKind;revision:number}[]{return [{id:scope.storyId,kind:'story',revision:scope.storyRevision},...(scope.branchId?[{id:scope.branchId,kind:'branch' as const,revision:scope.branchRevision!}]:[]),{id:scope.anchor.paragraphId,kind:'paragraph',revision:1},...scope.sources.flatMap(s=>[{id:s.id,kind:'paragraph' as const,revision:s.revision},{id:s.inputId,kind:'paragraph' as const,revision:s.inputId===s.id?s.revision:1}])]}
export type CharacterLookup=(id:string,revision?:number)=>{kind:DomainKind;revision?:number;value:unknown}|undefined
export function validateCharacterRelations(scope:CharacterScope,lookup:CharacterLookup){
 const story=lookup(scope.storyId,scope.storyRevision),branch=scope.branchId?lookup(scope.branchId,scope.branchRevision!):null
 if(story?.kind!=='story'||story.revision!==scope.storyRevision||scope.branchId&&(branch?.kind!=='branch'||branch.revision!==scope.branchRevision||(branch.value as {storyId:string}).storyId!==scope.storyId))fail('人物来源版本不存在')
 const line=(branch||story)!.value as LocalStory,anchorIndex=line.paragraphIds.indexOf(scope.anchor.paragraphId),anchor=lookup(scope.anchor.paragraphId)?.value as Paragraph|undefined
 if(anchorIndex<0||!anchor||scope.anchor.offset>anchor.text.length||unicodeOffset(anchor.text,scope.anchor.offset)!==scope.anchor.offset)fail('人物起点不在故事线内')
 let index=-1,end=0
 for(const source of scope.sources){const position=line.paragraphIds.indexOf(source.id),original=lookup(source.id,source.revision),input=lookup(source.inputId,source.inputId===source.id?source.revision:1)
  const start=source.start||0,v2=scope.schema==='novel-character-scope-v2'
  if(position<0||position<index||position===index&&(!v2||start<end)||position>anchorIndex||original?.kind!=='paragraph'||original.revision!==source.revision||input?.kind!=='paragraph'||input.revision!==(source.inputId===source.id?source.revision:1))fail('人物来源越过已读范围')
  const paragraph=original.value as Paragraph,copy=input.value as Paragraph
  if(source.limit>paragraph.text.length||source.text!==paragraph.text.slice(start,source.limit)||unicodeOffset(paragraph.text,start)!==start||unicodeOffset(paragraph.text,source.limit)!==source.limit||position===anchorIndex&&source.limit>scope.anchor.offset||copy.text!==source.text||v2&&copy.textHash!==source.sliceHash)fail('人物原文副本或已读偏移不一致')
  if(source.inputId!==source.id){
   if(v2){const slice=(copy as Paragraph&{sliceOf?:CharacterSlice}).sliceOf;if(!slice||slice.schema!=='novel-character-slice-v1'||slice.paragraphId!==source.id||slice.revision!==source.revision||slice.start!==start||slice.end!==source.limit||slice.offsetUnit!=='utf16'||copy.sourceStart!==paragraph.sourceStart+start||copy.sourceEnd!==paragraph.sourceStart+source.limit||copy.chapterId!==paragraph.chapterId)fail('人物切片没有正确的原文归属')}
   else{const fork=copy as Paragraph&{forkOf?:{paragraphId:string;offset:number}};if(fork.forkOf?.paragraphId!==source.id||fork.forkOf.offset!==source.limit||fork.chapterId!==paragraph.chapterId)fail('人物片段没有正确的原文归属')}
  }else if(start!==0||source.limit!==paragraph.text.length)fail('裁切人物原文必须保留独立切片')
  index=position;end=source.limit
 }
}
export function validateCharacterEvidence(character:NovelCharacter){
 const names=[character.name,...character.aliases.map(alias=>alias.name)],validate=(item:CharacterEvidence)=>{const source=character.scope.sources.find(source=>source.id===item.id&&source.revision===item.revision&&item.offset>=(source.start||0)&&item.offset+item.quote.length<=source.limit),offset=item.offset-(source?.start||0),end=offset+item.quote.length;if(!source||source.text.slice(offset,end)!==item.quote||unicodeOffset(source.text,offset)!==offset||unicodeOffset(source.text,end)!==end)fail('人物证据不是范围内的准确原文')}
 validate(character.primary)
 for(const alias of character.aliases){alias.evidence.forEach(validate);if(!alias.evidence.some(e=>e.quote.includes(alias.name)&&e.quote.includes(character.name)))fail('别名需要同时指出两种称呼的原文')}
 for(const fact of character.facts){fact.evidence.forEach(validate);if(!fact.evidence.some(e=>names.some(name=>e.quote.includes(name))))fail('人物状态必须有指向此人的证据')}
}
/** Names are labels. Identity equality requires the same exact source occurrence. */
export function sameCharacterAnchor(a:NovelCharacter,b:NovelCharacter){return a.scope.storyId===b.scope.storyId&&a.scope.branchId===b.scope.branchId&&a.name===b.name&&canonical(a.primary)===canonical(b.primary)}
export interface CharacterSlice {schema:'novel-character-slice-v1';paragraphId:string;revision:number;start:number;end:number;offsetUnit:'utf16'}
/** Historical assertions remain independent; only the read projection is combined. */
export function projectCharacterHistory(versions:NovelCharacter[],anchor:ProgressAnchor,line:LocalStory):NovelCharacter|null{
 if(!versions.length)return null
 const latest=versions.at(-1)!;for(const row of versions){validateNovelCharacter(row);validateCharacterEvidence(row);if(!sameCharacterAnchor(latest,row)||row.identityId!==latest.identityId||row.cardId!==latest.cardId)fail('人物历史身份不一致')}
 const visible=versions.map(row=>visibleCharacter(row,anchor,line)).filter((row):row is NovelCharacter=>!!row);if(!visible.length)return null
 const unique=<T>(rows:T[])=>[...new Map(rows.map(row=>[canonical(row),row])).values()]
 const result={...latest,aliases:unique(visible.flatMap(row=>row.aliases)),facts:unique(visible.flatMap(row=>row.facts)),firstMessage:canonical(anchor)===canonical(latest.scope.anchor)?latest.firstMessage:''}
 if(result.aliases.length>1000||result.facts.length>2000||new TextEncoder().encode(canonical({aliases:result.aliases,facts:result.facts})).byteLength>2_000_000)fail('人物历史投影超过上限，请先归档；未静默删掉事实')
 return result
}
export function visibleCharacter(character:NovelCharacter,anchor:ProgressAnchor,line:LocalStory){
 if(anchor.storyId!==character.scope.storyId||anchor.branchId!==character.scope.branchId)return null
 const end=line.paragraphIds.indexOf(anchor.paragraphId),allowed=(e:CharacterEvidence)=>{const pos=line.paragraphIds.indexOf(e.id);return pos>=0&&pos<=end&&(pos<end||e.offset+e.quote.length<=anchor.offset)}
 if(end<0||!allowed(character.primary))return null
 return {...character,aliases:character.aliases.filter(alias=>alias.evidence.every(allowed)),facts:character.facts.filter(fact=>fact.evidence.every(allowed))}
}
export function characterCard(character:NovelCharacter,origin:NovelCardOrigin,template:CharacterCard):CharacterCard{
 const label=(fact:CharacterFact)=>`${fact.confidence==='inferred'?'推测：':''}${fact.text}`
 const description=character.facts.filter(f=>['identity','life','appearance'].includes(f.kind)).map(label).join('\n'),personality=character.facts.filter(f=>f.kind==='personality').map(label).join('\n'),scenario=character.facts.filter(f=>f.kind==='scenario').map(label).join('\n')
 return {...JSON.parse(canonical(template)),identityId:character.identityId,name:character.name,description,personality,scenario,firstMessage:character.firstMessage,tags:[...new Set([...template.tags,'小说人物'])],data:{...template.data,name:character.name,description,personality,scenario,first_mes:character.firstMessage,extensions:{...((template.data.extensions||{}) as Record<string,unknown>),readerNovel:{format:origin.versions?'reader-novel-character-v2':'reader-novel-character-v1',...origin,aliases:character.aliases,facts:character.facts}}},novelOrigin:origin} as CharacterCard
}
/** Reproject generated facts while preserving the user's explicit card edits in new chats. */
export function readerCharacterCard(full:NovelCharacter,visible:NovelCharacter,origin:NovelCardOrigin,template:CharacterCard):CharacterCard{
 const baseline=characterCard(full,{...origin,anchor:full.scope.anchor},template),card=characterCard(visible,origin,template)
 for(const field of ['name','description','personality','scenario','firstMessage'] as const)if(template[field]!==baseline[field])card[field]=template[field]
 card.data={...card.data,name:card.name,description:card.description,personality:card.personality,scenario:card.scenario,first_mes:card.firstMessage,mes_example:card.exampleMessages,creator_notes:card.creatorNotes,system_prompt:card.systemPrompt,post_history_instructions:card.postHistoryInstructions,alternate_greetings:card.alternateGreetings}
 // A saved portable source may contain an earlier generated projection too.
 if(record(card.source.data))card.source.data={...card.source.data,...card.data}
 return card
}
