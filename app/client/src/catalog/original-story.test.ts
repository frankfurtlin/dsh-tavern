import {validateLocalValue} from '../domain/local-validation'
import {validateUpdate} from '../domain/mutation-policy'
import {describe,it,expect} from 'vitest'
import {newOriginalStoryDraft,originalParagraphs,readyOriginalStoryDraft,validateOriginalStoryDraft,validateOriginalDraftUpdate} from './original-story'
const starter=()=>({...newOriginalStoryDraft().value,title:'自己的故事',text:'第一句A😊B。\r\n\n  第二句留着空格。  \r\n'})
describe('durable original story creation contract',()=>{
 it('indexes exact UTF16 offsets without stripping source line separators or paragraph spaces',()=>{const text='\r\n  A😊B  \r\n\n第二句\r\n';const parts=originalParagraphs(text);expect(parts.map(p=>text.slice(p.start,p.end))).toEqual(['  A😊B  ','第二句']);expect(parts[0]).toEqual({text:'  A😊B  ',start:2,end:10})})
 it('allows an empty editing buffer but requires real readable text before freezing',()=>{const initial=newOriginalStoryDraft();expect(()=>validateOriginalStoryDraft(initial.value,initial.id)).not.toThrow();expect(()=>readyOriginalStoryDraft(initial.value)).toThrow();expect(()=>readyOriginalStoryDraft({...starter(),text:'\n \r\n'})).toThrow();expect(()=>readyOriginalStoryDraft({...starter(),text:'\uD800'})).toThrow();expect(()=>readyOriginalStoryDraft({...starter(),text:'\uDC00'})).toThrow()})
 it('rejects namespace masquerading, unexpected fields, aliased object identities and oversized text',()=>{const initial=newOriginalStoryDraft();expect(()=>validateOriginalStoryDraft(initial.value,'settings:wrong')).toThrow();expect(()=>validateOriginalStoryDraft({...initial.value,extra:'secret'})).toThrow();expect(()=>validateOriginalStoryDraft({...initial.value,sourceId:initial.value.storyId})).toThrow();expect(()=>validateOriginalStoryDraft({...initial.value,text:'字'.repeat(200001)})).toThrow()})
 it('freezes all body and identifiers until completion rather than reopening a terminal draft',()=>{const editing=starter(),ready=readyOriginalStoryDraft(editing),completed={...ready,status:'completed' as const};expect(()=>validateOriginalDraftUpdate(editing,ready)).not.toThrow();expect(()=>validateOriginalDraftUpdate(ready,completed)).not.toThrow();for(const patch of [{text:'另一句话'}, {operationId:'new-operation'}, {paragraphIds:[...ready.paragraphIds].reverse()}, {status:'editing'}])expect(()=>validateOriginalDraftUpdate(ready,{...ready,...patch} as typeof ready)).toThrow();expect(()=>validateOriginalDraftUpdate(completed,ready)).toThrow()})
 it('requires one unique paragraph identity per exact readable paragraph',()=>{const ready=readyOriginalStoryDraft(starter());expect(ready.paragraphIds).toHaveLength(2);expect(()=>validateOriginalStoryDraft({...ready,paragraphIds:[ready.paragraphIds[0]!,ready.paragraphIds[0]!]})).toThrow();expect(()=>validateOriginalStoryDraft({...ready,paragraphIds:ready.paragraphIds.slice(1)})).toThrow()})
})


describe('reserved original creation namespace and first typed history',()=>{
 it('rejects generic values and another kind under a reserved draft ID',()=>{
  const draft=newOriginalStoryDraft().value
  expect(()=>validateLocalValue('settings',{},'story-draft:own')).toThrow()
  expect(()=>validateLocalValue('settings',{assetType:'library-type',type:'story'},'story-draft:own')).toThrow()
  expect(()=>validateLocalValue('card',draft,'story-draft:own')).toThrow()
  expect(()=>validateLocalValue('settings',draft,'story-draft:own')).not.toThrow()
 })
 it('rejects even valid editing or ready drafts introduced after generic setting history',()=>{
  const draft=newOriginalStoryDraft().value,previous={id:'story-draft:own',kind:'settings' as const,revision:1,updatedAt:new Date().toISOString(),value:{}}
  expect(()=>validateUpdate('settings',previous,draft)).toThrow()
  expect(()=>validateUpdate('settings',previous,readyOriginalStoryDraft({...draft,title:'自己的标题',chapterTitle:'第一章',text:'自己的原句。'}))).toThrow()
 })
})
