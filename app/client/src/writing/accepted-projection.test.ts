import {describe,it,expect} from 'vitest'
import vectors from './__fixtures__/accepted-projection-vectors.json'
import {projectAcceptedText,validateAcceptedProjection,type AcceptanceStyle} from './accepted-projection'
import {acceptedWritingParagraphs} from './accepted-prose'
import {eventCanonical} from './events'
import {sha256} from '../domain/local-contract'

describe('exact accepted prose coordinates',()=>{
 for(const vector of vectors)it(vector.name+' matches Python canonical bytes and source coordinates',async()=>{
  const value=projectAcceptedText(vector.text,vector.style as AcceptanceStyle)
  expect(value).toEqual(vector.projection)
  expect(eventCanonical(value)).toBe(vector.canonical)
  expect(await sha256(eventCanonical(value))).toBe(vector.sha256)
  validateAcceptedProjection(value,vector.text,vector.style as AcceptanceStyle)
  for(const part of value.segments)expect(vector.text.slice(part.sourceStart,part.sourceEnd)).toBe(part.text)
 })
 it('keeps the existing acceptance text and normalized coordinates untouched',()=>{
  for(const vector of vectors.filter(v=>v.style==='local-v1')){
   if(!vector.projection.segments.length){expect(()=>acceptedWritingParagraphs({text:vector.text,raw:vector.text})).toThrow();continue}
   const parts=acceptedWritingParagraphs({text:vector.text,raw:vector.text})
   expect(parts.map(p=>p.text)).toEqual(vector.projection.segments.map(p=>p.text))
   let start=0
   for(const part of parts){expect(part.sourceStart).toBe(start);start=part.sourceEnd+2}
  }
 })
 it('rejects changed offsets or fabricated map fields when recomputed from original text',()=>{
  const vector=vectors[0]!,value=projectAcceptedText(vector.text,'local-v1')
  for(const patch of [{sourceStart:10},{sourceEnd:true},{targetEnd:1},{text:'甲'},{extra:'invented'}]){
   const copy=structuredClone(value);Object.assign(copy.segments[0]!,patch)
   expect(()=>validateAcceptedProjection(copy,vector.text,'local-v1')).toThrow()
  }
 })
 it('rejects broken UTF16 and unsupported acceptance styles',()=>{
  for(const body of ['\ud800','\udfff'])expect(()=>projectAcceptedText(body,'local-v1')).toThrow()
  expect(()=>projectAcceptedText('正文。','other' as AcceptanceStyle)).toThrow()
 })
})
