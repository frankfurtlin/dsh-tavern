import {vi} from 'vitest'
import type {SpeechDriver,SpeechState} from '../platform/speech-types'
// Contract callback fixture only. It does not synthesize or claim audio output.
export function speechEngine(){
  let key='',currentId='',listener:(value:SpeechState)=>void=()=>{}
  const driver:SpeechDriver={voices:vi.fn().mockResolvedValue({available:true,initializing:false,voices:[{id:'fixture-zh',name:'合成系统声音',language:'zh-CN',offline:true}]}),status:vi.fn().mockImplementation(async({scopeKey})=>{key=scopeKey;return {status:'idle',queueLoaded:false}}),loadQueue:vi.fn().mockImplementation(async options=>{key=options.scopeKey;currentId=options.fromId||options.segments[0].id;return {scopeKey:key,status:'ready',queueLoaded:true,currentId,eventVersion:1}}),play:vi.fn().mockImplementation(async()=>({scopeKey:key,status:'starting',queueLoaded:true,currentId,eventVersion:2})),pause:vi.fn().mockImplementation(async()=>({scopeKey:key,status:'paused',queueLoaded:true,currentId,eventVersion:6})),skip:vi.fn(),configure:vi.fn(),stop:vi.fn().mockResolvedValue(undefined),openSettings:vi.fn().mockResolvedValue({opened:false}),addListener:vi.fn().mockImplementation(async(_event,fn)=>{listener=fn;return {remove:vi.fn().mockResolvedValue(undefined)}})}
  return {driver,emit:(state:Partial<SpeechState>)=>listener({scopeKey:key,status:'playing',queueLoaded:true,currentId,eventVersion:3,...state})}
}
