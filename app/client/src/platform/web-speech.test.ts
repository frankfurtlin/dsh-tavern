import {beforeEach,afterEach,it,expect,vi} from 'vitest'
import {createWebSpeech} from './web-speech'
import {defaultSpeechPreferences,readingSpeechSegments,worldSpeechSegments,speechChunks,validateSpeechQueue,type SpeechSegment,type SpeechDriver} from './speech-types'
const anchor={bookId:'book',branchId:'line',revisionId:'revision',chapterId:'chapter',blockId:'a',start:3,end:3,offsetUnit:'utf16' as const,quoteHash:'hash'}
const segments:SpeechSegment[]=[{id:'a',text:'甲😀。',chapterId:'chapter',chapterTitle:'第一章',anchor},{id:'b',text:'乙。',chapterId:'chapter',chapterTitle:'第一章',anchor:{...anchor,blockId:'b'}},{id:'c',text:'丙。',chapterId:'next',chapterTitle:'第二章',anchor:{...anchor,chapterId:'next',blockId:'c'}}]
class Utterance {text:string;rate=1;voice:any;lang='';onstart?:()=>void;onend?:()=>void;onerror?:(event:{error:string})=>void;constructor(text:string){this.text=text}}
let driver:SpeechDriver,spoken:Utterance[],engine:any,voiceList:any[]
const options=(changes={})=>({...defaultSpeechPreferences,scopeKey:'scope',title:'合成测试书',segments,...changes})
beforeEach(()=>{vi.useFakeTimers();spoken=[];voiceList=[{voiceURI:'zh-fixture',name:'合成中文',lang:'zh-CN',localService:true,default:true}];engine=Object.assign(new EventTarget(),{cancel:vi.fn(),getVoices:()=>voiceList,speak:vi.fn((u:Utterance)=>spoken.push(u))});driver=createWebSpeech(engine,Utterance as any)})
afterEach(async()=>{await driver.stop({clearPosition:true});vi.useRealTimers()})
it('loads without speaking, and only actual start/end advances playback and confirmed paragraphs',async()=>{
  await driver.loadQueue(options());expect(spoken).toHaveLength(0);expect((await driver.status({scopeKey:'scope'})).status).toBe('ready')
  await driver.play({scopeKey:'scope'});expect((await driver.status({scopeKey:'scope'})).status).toBe('starting');expect((await driver.status({scopeKey:'scope'})).lastCompletedId).toBeUndefined()
  spoken[0].onstart?.();expect((await driver.status({scopeKey:'scope'})).status).toBe('playing');spoken[0].onend?.();expect((await driver.status({scopeKey:'scope'})).lastCompletedId).toBe('a');expect(spoken[1].text).toBe('乙。')
})
it('pause and manual skip invalidate old callbacks and never count a skipped paragraph as completed',async()=>{
  await driver.loadQueue(options());await driver.play({scopeKey:'scope'});const old=spoken[0];old.onstart?.();await driver.pause({scopeKey:'scope'});old.onend?.();expect((await driver.status({scopeKey:'scope'})).lastCompletedId).toBeUndefined()
  await driver.skip({scopeKey:'scope',direction:1});expect((await driver.status({scopeKey:'scope'})).currentId).toBe('b');expect(spoken).toHaveLength(1);await driver.play({scopeKey:'scope'});expect(spoken.at(-1)?.text).toBe('乙。')
})
it('stops at the chapter boundary before speaking its next chapter',async()=>{
  await driver.loadQueue(options({fromId:'b',stopAtChapterEnd:true}));await driver.play({scopeKey:'scope'});spoken[0].onstart?.();spoken[0].onend?.();const value=await driver.status({scopeKey:'scope'});expect(value.lastCompletedId).toBe('b');expect(value.reason).toBe('chapter-end');expect(value.status).toBe('paused');expect(spoken).toHaveLength(1)
})
it('an explicit resume after the sleep timer starts a new timer rather than immediately pausing again',async()=>{
  await driver.loadQueue(options({sleepMinutes:5}));await driver.play({scopeKey:'scope'});spoken[0].onstart?.();await vi.advanceTimersByTimeAsync(300000);await driver.play({scopeKey:'scope'});spoken.at(-1)?.onstart?.();await vi.advanceTimersByTimeAsync(1);expect((await driver.status({scopeKey:'scope'})).status).toBe('playing');expect((await driver.status({scopeKey:'scope'})).lastCompletedId).toBeUndefined()
})
it.each([5,15,30,60])('the actual %s minute sleep deadline pauses rather than manufacturing a completion',async(minutes)=>{
  await driver.loadQueue(options({sleepMinutes:minutes}));await driver.play({scopeKey:'scope'});spoken[0].onstart?.();await vi.advanceTimersByTimeAsync(minutes*60000);const value=await driver.status({scopeKey:'scope'});expect(value.status).toBe('paused');expect(value.reason).toBe('sleep-timer');expect(value.lastCompletedId).toBeUndefined()
})
it('uses a real available fallback and applies requested speed, and voiceschanged never starts audio',async()=>{
  await driver.loadQueue(options({voiceId:'absent-on-this-device',rate:1.75}));await driver.play({scopeKey:'scope'});expect(spoken[0].rate).toBe(1.75);const value=await driver.status({scopeKey:'scope'});expect(value.voiceFallback).toBe(true);expect(value.resolvedVoiceId).toBe('zh-fixture')
  await driver.pause({scopeKey:'scope'});const listener=vi.fn();await driver.addListener('speechState',listener);engine.dispatchEvent(new Event('voiceschanged'));expect(spoken).toHaveLength(1);expect(listener.mock.calls.at(-1)?.[0].reason).toBe('voices-changed')
})
it('missing Chinese voices and a missing start callback fail recoverably without pretending to play',async()=>{
  voiceList=[{voiceURI:'en',name:'English',lang:'en-US',localService:true}];await driver.loadQueue(options());await driver.play({scopeKey:'scope'});expect(spoken).toHaveLength(0);expect((await driver.status({scopeKey:'scope'})).reason).toBe('NO_CHINESE_SYSTEM_VOICE')
  voiceList=[{voiceURI:'zh',name:'中文',lang:'zh-CN',localService:true}];await driver.play({scopeKey:'scope'});await vi.advanceTimersByTimeAsync(15000);expect((await driver.status({scopeKey:'scope'})).reason).toBe('SYSTEM_SPEECH_START_TIMEOUT');expect((await driver.status({scopeKey:'scope'})).lastCompletedId).toBeUndefined()
})
it('a synchronous system synthesis failure remains its real error instead of a later fictitious start timeout',async()=>{
  engine.speak.mockImplementation(()=>{throw new Error('synthetic engine failure')});await driver.loadQueue(options());await driver.play({scopeKey:'scope'});await vi.advanceTimersByTimeAsync(15000);const value=await driver.status({scopeKey:'scope'});expect(value.status).toBe('error');expect(value.reason).toBe('SYSTEM_SPEECH_FAILED');expect(value.lastCompletedId).toBeUndefined()
})
it('only the final engine chunk confirms its paragraph, and a pause resumes from that paragraph start',async()=>{
  const text='甲'.repeat(1799)+'😀'+ '乙'.repeat(1900);await driver.loadQueue(options({segments:[{...segments[0],text}]}));await driver.play({scopeKey:'scope'});spoken[0].onstart?.();spoken[0].onend?.();expect((await driver.status({scopeKey:'scope'})).lastCompletedId).toBeUndefined();expect(spoken).toHaveLength(2)
  await driver.pause({scopeKey:'scope'});await driver.play({scopeKey:'scope'});expect(spoken.at(-1)?.text).toBe(spoken[0].text);expect(speechChunks(text).join('')).toBe(text);expect(speechChunks(text).every(s=>!/[\uD800-\uDBFF]$/.test(s))).toBe(true)
})
it('invalid replacement keeps the old scope and source; stop clears the queue and rejects late utterance events',async()=>{
  await driver.loadQueue(options());await driver.play({scopeKey:'scope'});const old=spoken[0];await expect(driver.loadQueue(options({scopeKey:'next',segments:[{...segments[0],text:'\ud800'}]}))).rejects.toThrow('SPEECH_QUEUE_INVALID');expect((await driver.status({scopeKey:'scope'})).currentId).toBe('a')
  await driver.stop({clearPosition:true});old.onstart?.();old.onend?.();expect((await driver.status({scopeKey:'scope'})).queueLoaded).toBe(false);expect((await driver.status({scopeKey:'scope'})).lastCompletedId).toBeUndefined()
})
it('builds only canonical prose and bounded public anchors, never HTML, images or private world fields',()=>{
  const blocks=readingSpeechSegments({id:'chapter',title:'title',blocks:[{id:'a',text:'正文',html:'<img private>',source:'original',startAnchor:anchor,endAnchor:{...anchor,privateKey:'bad'} as any}]});expect(blocks[0].text).toBe('正文');expect(JSON.stringify(blocks)).not.toContain('privateKey');expect(JSON.stringify(blocks)).not.toContain('<img')
  const cp={id:'cp',worldId:'world',lineId:'line',title:'场景',text:'甲😀。\n\n乙。',state:{gm:'private'},sequence:1,kind:'action'} as any,world=worldSpeechSegments('world','line',[cp]);expect(world.map(s=>s.text)).toEqual(['甲😀。','乙。']);expect(JSON.stringify(world)).not.toContain('private');expect(world[1].anchor.start).toBe(6)
  expect(()=>validateSpeechQueue(options({segments:[{...segments[0],anchor:{...anchor,state:{gm:'bad'}}}]}))).toThrow('SPEECH_QUEUE_INVALID')
})
