import {registerPlugin} from '@capacitor/core'
import {native,accountId,backendBase} from './bridge'
import {authGeneration} from '../api'
import {createWebSpeech} from './web-speech'
import type {SpeechDriver} from './speech-types'
import type {ReaderBackupBridgeWindow} from './reader-backup-document'
import {installSpeechDocumentGate,speechErrorMatchesContext} from './speech-document'
type NativeCall={epoch:number;documentNonce:string}
type NativeSpeechPlugin={
 capabilities(value:{documentNonce:string}):Promise<unknown>
 setContext(value:NativeCall&{owner:string;origin:string}):Promise<unknown>
 revokeContext(value:NativeCall):Promise<unknown>
 addListener:SpeechDriver['addListener']
}&{[K in Exclude<keyof SpeechDriver,'addListener'>]:(options:object)=>Promise<unknown>}
const plugin=registerPlugin<NativeSpeechPlugin>('ReaderSpeech')
const documentNonce=(()=>{
 if(!native)return ''
 const name='__readerSpeechDocumentNonce',existing=(window as unknown as Record<string,unknown>)[name]
 if(typeof existing==='string'&&/^[a-f0-9]{32}$/.test(existing))return existing
 const nonce=[...crypto.getRandomValues(new Uint8Array(16))].map(value=>value.toString(16).padStart(2,'0')).join('')
 Object.defineProperty(window,name,{value:nonce,writable:false,configurable:false,enumerable:false});return nonce
})()
type Context={owner:string;origin:string;auth:number;epoch:number}
let epoch=-1,current:Context|undefined,established:Context|undefined,contextWork:Promise<unknown>=Promise.resolve()
const changed=()=>Object.assign(new Error('账号或服务器已变化，请重新打开正文后播放'),{code:'SPEECH_CONTEXT_CHANGED'})
const documentChanged=()=>Object.assign(new Error('原生听书通道已变化，请重新打开'),{code:'SPEECH_DOCUMENT_CHANGED'})
const unavailable=()=>Object.assign(new Error('此安装包尚未提供安全的原生听书通道'),{code:'NATIVE_SPEECH_PROTOCOL_UNAVAILABLE'})
const same=(value:Context)=>value===current&&value.owner===accountId()&&value.origin===backendBase()&&value.auth===authGeneration()
function capture(){
 const owner=accountId(),origin=backendBase(),auth=authGeneration()
 if(!/^[a-f0-9]{32}$/.test(owner)||!origin)throw changed()
 if(!current||current.owner!==owner||current.origin!==origin||current.auth!==auth){current={owner,origin,auth,epoch:++epoch};established=undefined}
 return current
}
function protect(){const result=installSpeechDocumentGate(window as unknown as ReaderBackupBridgeWindow,documentNonce,()=>current?.epoch??(epoch>=0?epoch:undefined));if(!result.legacy&&!result.modern)throw documentChanged()}
function signed(value:unknown,expectedEpoch?:number):value is Record<string,unknown>{return !!value&&typeof value==='object'&&!Array.isArray(value)&&(value as Record<string,unknown>).documentProtocol==='reader-document-v1'&&(value as Record<string,unknown>).documentNonce===documentNonce&&(expectedEpoch===undefined||(value as Record<string,unknown>).contextEpoch===expectedEpoch)}
function body<T>(value:unknown,expectedEpoch?:number):T{if(!signed(value,expectedEpoch))throw documentChanged();const {documentProtocol:_,documentNonce:__,contextEpoch:___,...data}=value;return data as T}
function timed<T>(work:Promise<T>):Promise<T>{return new Promise((resolve,reject)=>{const timeout=setTimeout(()=>reject(documentChanged()),15000);work.then(resolve,reject).finally(()=>clearTimeout(timeout))})}
async function call<T>(operation:()=>Promise<unknown>,value?:Context,expectedEpoch:number|null|undefined=value?.epoch):Promise<T>{
 const checkedEpoch=expectedEpoch===null?undefined:expectedEpoch
 try{const result=await timed(operation());if(value&&!same(value))throw changed();return body<T>(result,checkedEpoch)}
 catch(error){
  if(value&&!same(value))throw changed()
  if((error as {code?:unknown})?.code==='SPEECH_DOCUMENT_CHANGED')throw error
  if(!speechErrorMatchesContext(error,documentNonce,checkedEpoch))throw documentChanged()
  const row=error as {code?:unknown;message?:unknown};throw Object.assign(new Error(typeof row.message==='string'?row.message:'系统听书暂时不可用'),{code:typeof row.code==='string'?row.code:'SYSTEM_SPEECH_FAILED'})
 }
}
async function context(){
 const value=capture();protect()
 const request=contextWork.catch(()=>{}).then(async()=>{
  if(!same(value))throw changed();if(established===value)return value
  let capability:{ready?:unknown;contextRequired?:unknown}
  try{capability=await call(()=>plugin.capabilities({documentNonce}),value,null)}catch(error){if(!same(value))throw changed();throw unavailable()}
  if(capability.ready!==true||capability.contextRequired!==true)throw unavailable()
  const answer=await call<{ready:boolean}>(()=>plugin.setContext({owner:value.owner,origin:value.origin,epoch:value.epoch,documentNonce}),value)
  if(answer.ready!==true)throw unavailable();established=value;return value
 });contextWork=request;return request
}
async function business<K extends Exclude<keyof SpeechDriver,'addListener'>>(name:K,options:object):Promise<Awaited<ReturnType<SpeechDriver[K]>>>{const value=await context();if(!same(value))throw changed();return await call<Awaited<ReturnType<SpeechDriver[K]>>>(()=>plugin[name]({...options,epoch:value.epoch,documentNonce}),value)}
const readerSpeech:SpeechDriver={
 voices:async options=>{try{return await business('voices',options||{})}catch(error){if((error as {code?:unknown}).code==='NATIVE_SPEECH_PROTOCOL_UNAVAILABLE')return {voices:[],available:false,initializing:false,reason:'NATIVE_SPEECH_PROTOCOL_UNAVAILABLE'};throw error}},
 status:value=>business('status',value),loadQueue:value=>business('loadQueue',value),play:value=>business('play',value),pause:value=>business('pause',value),skip:value=>business('skip',value),configure:value=>business('configure',value),stop:value=>business('stop',value),openSettings:()=>business('openSettings',{}),
 addListener:async(event,listener)=>{
  const value=await context();if(!same(value))throw changed()
  const handle=await plugin.addListener(event,input=>{if(same(value)&&signed(input,value.epoch))listener(body(input,value.epoch))})
  if(!same(value)){await handle.remove();throw changed()}
  return handle
 }
}
function revoke(){
 if(!native)return
 const revoked=++epoch;current=undefined;established=undefined
 // Do not queue revocation behind a held business response. The native gate
 // invalidates old captured commands and old markers before any later identity.
 try{protect();void call(()=>plugin.revokeContext({epoch:revoked,documentNonce}),undefined,revoked).catch(()=>{})}catch{}
}
if(native){try{protect()}catch{}for(const event of ['reader-private-cleared','reader-backend-changed'])window.addEventListener(event,revoke)}
let web:SpeechDriver|undefined
export function systemSpeech():SpeechDriver{return native?readerSpeech:web||(web=createWebSpeech())}
