import {it,expect} from 'vitest'
import {compileParagraphsToBeats} from './compiler'
import {sha256,unicodeOffset} from '../domain/local-contract'
const characters=[{id:'shen',name:'沈棠',aliases:[]},{id:'xu',name:'许知',aliases:['小许']}]
async function paragraph(text:string,id='p'){return {id,chapterId:'c',text,sourceStart:0,sourceEnd:text.length,textHash:await sha256(text)}}
it('preserves every source character and exact UTF16 spans',async()=>{
  const texts=['沈棠说：“回家吧。”雾慢慢散开。','「好。」许知低声说。','小许问：“明天见？”','她拿起信，说了一个无人知道的名字。','“我答应你。”没有给出姓名。','一段未闭合的引语“仍然完整保留原文。','emoji 🌿😀 与换行\n都要保留。','“甲。”沈棠说，“乙。”许知回答。']
  for(const text of texts){const source=await paragraph(text),compiled=await compileParagraphsToBeats([source],characters);expect(compiled.beats.map(b=>b.text).join('')).toBe(text);let cursor=0;for(const beat of compiled.beats){expect(beat.start).toBe(cursor);expect(text.slice(beat.start,beat.end)).toBe(beat.text);expect(unicodeOffset(text,beat.start)).toBe(beat.start);expect(unicodeOffset(text,beat.end)).toBe(beat.end);cursor=beat.end}expect(cursor).toBe(text.length)}
})
it('attributes only explicit registered speakers and leaves ambiguity to narrator',async()=>{
  expect((await compileParagraphsToBeats([await paragraph('沈棠低声说：“回家。”')],characters)).beats.find(b=>b.text==='“回家。”')?.speakerId).toBe('shen')
  expect((await compileParagraphsToBeats([await paragraph('“好。”小许回答。')],characters)).beats[0]?.speakerId).toBe('xu')
  expect((await compileParagraphsToBeats([await paragraph('“好。”某个陌生人回答。')],characters)).beats[0]?.speakerId).toBeNull()
  const ambiguous=[...characters,{id:'other',name:'沈棠',aliases:[]}]
  expect((await compileParagraphsToBeats([await paragraph('沈棠说：“好。”')],ambiguous)).beats.find(b=>b.kind==='dialogue')).toBeUndefined()
})
it('has deterministic hashes and changes identity when source or compiler inputs change',async()=>{
  const source=await paragraph('沈棠说：“好。”')
  const a=await compileParagraphsToBeats([source],characters),b=await compileParagraphsToBeats([source],characters)
  expect(a).toEqual(b);expect((await compileParagraphsToBeats([source],[])).hash).not.toBe(a.hash)
  expect((await compileParagraphsToBeats([await paragraph(source.text+'。')],characters)).beats.at(-1)?.id).not.toBe(a.beats.at(-1)?.id)
})
