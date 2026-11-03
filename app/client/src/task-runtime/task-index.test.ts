import {describe,it,expect} from 'vitest'
import type {Document,DomainKind} from '../domain/local-contract'
import {buildTaskIndex,filterTaskIndex,taskBody,taskIndexEntry} from './task-index'
const at='2026-10-06T12:00:00.000Z',doc=(id:string,kind:DomainKind,value:unknown):Document=>({id,kind,value,revision:1,updatedAt:at})
const model={modelId:'自己的模型',key:'synthetic-private-credential',protocol:'responses'},story=doc('book','story',{title:'自己的原文'}),session=doc('session','session',{title:'自己的角色聊天'}),lookup=(id:string)=>[story,session].find(d=>d.id===id)
describe('unified local task projection',()=>{
 it('indexes real record types without treating model configurations or plans as executed jobs',()=>{
  const rows=buildTaskIndex([story,session,doc('model:1','settings',{...model,assetType:'media-provider'}),doc('unrelated','settings',{name:'not a job'}),doc('draft','settings',{assetType:'original-story-draft',title:'原创',text:'A😊B\r\n',status:'ready',storyId:'unallocated'}),doc('import','settings',{assetType:'import-entry',status:'needs_file',name:'待导入.txt',storyId:null,retired:false}),doc('play','settings',{assetType:'playable-intent',status:'ready',type:'create',worldId:'not-created',definition:{name:'雾港'}})])
  expect(rows).toHaveLength(3);expect(rows.map(r=>r.module).sort()).toEqual(['creation','creation','import']);expect(rows.every(r=>r.attempts===0)).toBe(true);expect(rows.find(r=>r.id==='play')?.destination?.query).toEqual({intent:'play'})
 })
 it('keeps received fragments, source route and unknown usage without claiming completion',()=>{
  const row=doc('writing-run:1','settings',{assetType:'writing-run',status:'unknown',storyId:'book',parentBranchId:'branch',anchor:{paragraphId:'p',offset:17},configSnapshot:model,chapters:[{title:'完整第一章',status:'complete',text:'完整原文',attemptId:'one',usage:{input:23,output:9},providerRequestId:'original-request'},{title:'第二章',status:'partial',text:'保留片段',attemptId:'two',usage:{input:null,output:null},providerRequestId:null}],historical:false})
  const entry=taskIndexEntry(row,lookup)!;expect(entry.state).toBe('unknown');expect(entry.attempts).toBe(2);expect(entry.input).toBeNull();expect(entry.output).toBeNull();expect(entry.hasText).toBe(true);expect(entry.destination).toEqual({path:'/local/read/book',query:{branch:'branch',paragraph:'p',offset:'17',task:row.id}});expect(taskBody(row).sections.map(s=>s.text)).toEqual(['完整原文','保留片段']);expect(taskBody(row).requestIds).toEqual(['original-request'])
 })
 it('does not expose frozen prompts, nested provider keys or arbitrary tool output',()=>{
  const row=doc('agent','agentRun',{status:'unknown',instruction:'自己的任务',sessionId:'missing',configSnapshot:{value:model},request:[{content:'private prompt'}],plan:[],results:[{tool:'read',summary:'已读取',data:{key:'arbitrary-secret'}}],provider:{attemptId:'attempt',requestId:'request',output:'key/token文字 '+model.key,input:null,outputTokens:null},error:'失败 '+model.key})
  const view=JSON.stringify([taskIndexEntry(row,lookup),taskBody(row)]);expect(view).not.toContain(model.key);expect(view).not.toContain('private prompt');expect(view).not.toContain('arbitrary-secret');expect(view).toContain('key/token文字');expect(view).toContain('[凭据已隐藏]')
 })
 it('redacts each actual frozen skill slot credential without scrubbing ordinary item names',()=>{
  const row=doc('skill','settings',{assetType:'skill-run',status:'ready',manifest:{name:'整理角色'},attemptId:'one',provider:{requestId:'request',input:10,output:null},resourceIds:[],raw:'物品 key 和 token。 synthetic-image-key',output:null,configSnapshot:{text:{value:model},image:{value:{key:'synthetic-image-key'}}},error:model.key})
  expect(taskIndexEntry(row,lookup)?.state).toBe('approval');expect(taskIndexEntry(row,lookup)?.error).toBe('[凭据已隐藏]');expect(taskBody(row).sections[0]!.text).toBe('物品 key 和 token。 [凭据已隐藏]')
 })
 it('does not make restored history cancellable or invent missing source titles',()=>{
  const row=doc('task','task',{kind:'chat',status:'running',configSnapshot:model,sessionId:'missing',historical:true,usage:{input:null,output:null},attemptId:'one',events:[],text:'已经收到'})
  const entry=taskIndexEntry(row,lookup)!;expect(entry.canStop).toBe(false);expect(entry.destination).toBeNull();expect(entry.sourceTitle).toBeNull();expect(taskBody(row).sections[0]!.text).toBe('已经收到')
 })
 it('filters source, type and state together while keeping deterministic order',()=>{
  const rows=buildTaskIndex([story,doc('z','settings',{assetType:'original-story-draft',title:'Alpha',text:'',status:'completed',storyId:'book'}),doc('a','settings',{assetType:'import-entry',name:'Beta',retired:false,status:'failed',storyId:null})]);expect(rows.map(r=>r.id)).toEqual(['a','z']);expect(filterTaskIndex(rows,' 原文 ','creation','completed').map(r=>r.id)).toEqual(['z']);expect(filterTaskIndex(rows,'beta','import','failed').map(r=>r.id)).toEqual(['a']);expect(filterTaskIndex(rows,'','creation','failed')).toEqual([])
 })
 it('shows per-job attempts and child links rather than adding orchestration counters to provider calls',()=>{
  const row=doc('skill','settings',{assetType:'skill-run',status:'running',manifest:{name:'漫画'},resourceIds:['media-job:1','media-job:2'],calls:3,attemptId:'orchestration',raw:'',output:null,configSnapshot:null,provider:null})
  expect(taskIndexEntry(row,lookup)?.attempts).toBeNull();expect(taskBody(row).children).toEqual(['media-job:1','media-job:2']);const group=doc('group','settings',{assetType:'group-run',state:'paused',sessionId:'session',started:3});expect(taskIndexEntry(group,lookup)?.attempts).toBeNull();expect(taskIndexEntry(group,lookup)?.state).toBe('paused')
 })
})
