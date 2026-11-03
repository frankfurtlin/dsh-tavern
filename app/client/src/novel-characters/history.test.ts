import {describe,it,expect} from 'vitest'
import {canonical,sha256,type Document,type Paragraph,type LocalStory,type ProgressAnchor} from '../domain/local-contract'
import type {LocalVault} from '../repositories/local-vault'
import {emptyCard} from '../tavern/card-format'
import {characterChunk,characterSlice,type CharacterCursor} from './chunking'
import {parseCharacters} from './runtime'
import {projectCharacterHistory,characterCard,validateNovelCardMetadata,validateCharacterScope,validateCharacterRelations,type CharacterScope,type NovelCharacter} from './contract'
import {novelReferences,validateNovelRelations} from './relations'
import {validateLocalValue} from '../domain/local-validation'

async function fixture(texts:string[]){
 const paragraphs:Document<Paragraph>[]=[];for(const [i,text] of texts.entries())paragraphs.push({id:'p'+i,kind:'paragraph',revision:1,updatedAt:'test',value:{id:'p'+i,chapterId:'chapter',text,sourceStart:0,sourceEnd:text.length,textHash:await sha256(text)}})
 const story:Document<LocalStory>={id:'book',kind:'story',revision:1,updatedAt:'test',value:{title:'own',origin:'original',immutableSource:false,format:'txt',encoding:'utf-8',sourceHash:'a'.repeat(64),tags:[],favorite:false,chapters:[{id:'chapter',title:'one',paragraphIds:paragraphs.map(p=>p.id)}],paragraphIds:paragraphs.map(p=>p.id)}}
 const records=new Map<string,Document>([story,...paragraphs].map(row=>[row.id,row]));const vault={read:async(id:string)=>records.get(id)||null} as unknown as LocalVault
 const anchor:ProgressAnchor={storyId:'book',branchId:null,paragraphId:paragraphs.at(-1)!.id,offset:paragraphs.at(-1)!.value.text.length}
 return {story,paragraphs,records,vault,anchor}
}
function person(scope:CharacterScope,sourceIndex:number,quote:string,fact:string){
 const source=scope.sources[sourceIndex]!,offset=source.text.indexOf(quote),primary=scope.sources[0]!
 return parseCharacters(JSON.stringify({cards:[{name:'林渡',mention:{id:primary.inputId,revision:primary.inputId===primary.id?primary.revision:1,offset:primary.text.indexOf('林渡')},aliases:[],facts:[{kind:'identity',text:fact,confidence:'explicit',evidence:[{id:source.inputId,revision:source.inputId===source.id?source.revision:1,offset,quote}]}]}]}),scope,'scan')[0]!
}
describe('source slices and cumulative character history',()=>{
 it('covers 100 paragraphs and a >64k paragraph without gaps, duplicates, or surrogate splitting',async()=>{
  const f=await fixture(['字'.repeat(47999)+'🌊'+'尾'.repeat(80000),...Array.from({length:100},(_,i)=>'第'+i+'段🌿')]);let cursor:CharacterCursor|undefined,combined='',count=0
  while(true){const chunk=await characterChunk(f.vault,'book',null,f.anchor,cursor);if(!chunk)break;expect(chunk.scope.sources.length).toBeLessThanOrEqual(16);expect(chunk.characters).toBeLessThanOrEqual(48000);combined+=chunk.scope.sources.map(s=>s.text).join('');count++;for(const s of chunk.scope.sources){expect(s.sliceHash).toBe(await sha256(s.text));expect(s.text).not.toMatch(/^[\uDC00-\uDFFF]|[\uD800-\uDBFF]$/)}if(!chunk.next)break;cursor=chunk.next}
  expect(combined).toBe(f.paragraphs.map(p=>p.value.text).join(''));expect(count).toBeGreaterThan(6)
 })
 it('stops exactly at the authorized read point, including inside a later paragraph',async()=>{
  const f=await fixture(['已读'.repeat(26000),'林渡归来。UNREAD_SECRET','未读章']);const anchor={...f.anchor,paragraphId:'p1',offset:5};let cursor:CharacterCursor|undefined,text=''
  while(true){const chunk=await characterChunk(f.vault,'book',null,anchor,cursor);if(!chunk)break;text+=chunk.scope.sources.map(s=>s.text).join('');if(!chunk.next)break;cursor=chunk.next}expect(text).toBe(f.paragraphs[0]!.value.text+'林渡归来。');expect(text).not.toContain('UNREAD')
 })
 it('rejects foreign, backwards, and half-surrogate cursors before preparing a model task',async()=>{
  const f=await fixture(['🌊林渡']);for(const cursor of [{paragraphId:'foreign',offset:0},{paragraphId:'p0',offset:-1},{paragraphId:'p0',offset:1},{paragraphId:'p0',offset:99}])await expect(characterChunk(f.vault,'book',null,f.anchor,cursor)).rejects.toThrow();await expect(characterChunk(f.vault,'book',null,{...f.anchor,branchId:'foreign'})).rejects.toThrow()
 })
 it('maps model-local offsets from a suffix back to exact original UTF16 evidence',async()=>{
  const f=await fixture(['前文🌊。林渡是医生。']);const source=await characterSlice(f.paragraphs[0]!,5,f.anchor.offset),scope:CharacterScope={schema:'novel-character-scope-v2',storyId:'book',storyRevision:1,branchId:null,branchRevision:null,anchor:f.anchor,sources:[source]},p=person(scope,0,'林渡是医生。','医生');expect(p.primary.offset).toBe(5);expect(p.facts[0]!.evidence[0]!.offset).toBe(5)
  const {schema,...missing}=scope;expect(()=>validateCharacterScope(missing)).toThrow();expect(()=>validateCharacterScope({...scope,schema:null})).toThrow()
 })
 it('validates exact slice provenance, declared hash, ordered intervals and source revision',async()=>{
  const f=await fixture(['林渡是医生。']),source=await characterSlice(f.paragraphs[0]!,2,6),scope:CharacterScope={schema:'novel-character-scope-v2',storyId:'book',storyRevision:1,branchId:null,branchRevision:null,anchor:f.anchor,sources:[source]},slice={schema:'novel-character-slice-v1',paragraphId:'p0',revision:1,start:2,end:6,offsetUnit:'utf16'},value={id:source.inputId,chapterId:'chapter',text:source.text,sourceStart:2,sourceEnd:6,textHash:source.sliceHash,sliceOf:slice}
  f.records.set(source.inputId,{id:source.inputId,kind:'paragraph',revision:1,updatedAt:'test',value});const lookup=(id:string,revision?:number)=>{const row=f.records.get(id);return row&&(!revision||row.revision===revision)?row:undefined};expect(()=>validateCharacterRelations(scope,lookup)).not.toThrow();expect(()=>validateNovelRelations('paragraph',source.inputId,value,lookup)).not.toThrow();expect(novelReferences('paragraph',value)).toContainEqual({id:'p0',kind:'paragraph',revision:1})
  expect(()=>validateCharacterRelations({...scope,sources:[{...source,sliceHash:'b'.repeat(64)}]},lookup)).toThrow();expect(()=>validateCharacterRelations({...scope,sources:[source,{...source,inputId:'duplicate'}]},lookup)).toThrow();expect(()=>validateNovelRelations('paragraph',source.inputId,{...value,sliceOf:{...slice,revision:2}},lookup)).toThrow();expect(()=>validateLocalValue('paragraph',{...value,forkOf:{paragraphId:'p0',offset:6}})).toThrow()
 })
 it('restores earlier assertions after a newer limited scope loses them, while clipping future facts on rewind',async()=>{
  const f=await fixture(['林渡是南岸摆渡人。','林渡后来成为医生。']);const original:CharacterScope={storyId:'book',storyRevision:1,branchId:null,branchRevision:null,anchor:{...f.anchor,paragraphId:'p0',offset:f.paragraphs[0]!.value.text.length},sources:[{id:'p0',revision:1,inputId:'p0',limit:f.paragraphs[0]!.value.text.length,text:f.paragraphs[0]!.value.text}]},a=person(original,0,'林渡是南岸摆渡人。','南岸摆渡人');a.identityId='same';a.cardId='same-card'
  const later:CharacterScope={...original,schema:'novel-character-scope-v2',anchor:f.anchor,sources:[await characterSlice(f.paragraphs[0]!,0,2),await characterSlice(f.paragraphs[1]!,0,f.anchor.offset)]},b=person(later,1,'林渡后来成为医生。','医生');b.identityId=a.identityId;b.cardId=a.cardId
  expect(b.facts.map(f=>f.text)).toEqual(['医生']);const all=projectCharacterHistory([a,b],f.anchor,f.story.value)!;expect(all.facts.map(f=>f.text)).toEqual(['南岸摆渡人','医生']);const early=projectCharacterHistory([a,b],original.anchor,f.story.value)!;expect(early.facts.map(f=>f.text)).toEqual(['南岸摆渡人']);expect(projectCharacterHistory([a,b],{...f.anchor,branchId:'other'},f.story.value)).toBeNull();expect(()=>projectCharacterHistory([a,{...b,identityId:'same-name-other-person'}],f.anchor,f.story.value)).toThrow()
  const origin={characterId:'person',characterRevision:2,anchor:f.anchor,versions:[{characterId:'person',characterRevision:1},{characterId:'person',characterRevision:2}]},card=characterCard(all,origin,emptyCard());expect(card.description).toContain('南岸摆渡人');expect(()=>validateNovelCardMetadata(card as unknown as Record<string,unknown>)).not.toThrow();expect(canonical(card.data)).toContain('reader-novel-character-v2');card.novelOrigin!.versions!.splice(0,1);expect(()=>validateNovelCardMetadata(card as unknown as Record<string,unknown>)).toThrow()
 })
})
