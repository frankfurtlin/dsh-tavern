import {expect,it} from 'vitest'
import {canonical,type ProviderConfiguration} from '../domain/local-contract'
import {validateLocalRelations} from '../domain/local-validation'
import {validateUpdate} from '../domain/mutation-policy'
import {validateWritingModel} from './model-contract'
import {validateWritingRun,type WritingRun,type LocalBranch} from './contract'
import {originalWritingPhases} from './migration-contract'
const profile:ProviderConfiguration={id:'model:owned',name:'owned',protocol:'chat-completions',baseUrl:'http://127.0.0.1:1/v1',modelId:'owned',key:'OWNED_RANDOM_CREDENTIAL',enabled:true,contextLimit:10000,outputLimit:2000,slots:['text'],source:'fixture',verifiedAt:'2026-10-06',dailyCalls:3,firstPacketMs:1000,idleMs:1000,totalMs:10000}
const config={id:profile.id,revision:1,protocol:profile.protocol,baseUrl:profile.baseUrl,modelId:profile.modelId,key:profile.key,contextLimit:profile.contextLimit,outputLimit:profile.outputLimit,outputParameter:'max_tokens' as const,includeUsage:false,queryStoredResponse:false},timeouts={firstPacketMs:1000,idleMs:1000,totalMs:10000}
const prefix=[{id:'c1',title:'渡口',paragraphIds:['p1']}],anchor={storyId:'book',branchId:null,paragraphId:'p1',offset:2},id='writing-run:owned'
function fixture(){
 const run:WritingRun={assetType:'writing-run',storyId:'book',storyRevision:1,parentBranchId:null,parentRevision:1,anchor,prefixChapters:prefix,prefixParagraphId:null,targetBranchId:'branch',targetRevision:0,append:false,direction:'',chapterCount:1,length:1000,style:'',forbidden:'',authorNote:'',configSnapshot:config,timeouts,dailyCalls:3,chapters:[{id:'c2',title:'来信',paragraphIds:['p2','p3'],text:'来信仍在旅人手中。\n\n他走到钟楼。',status:'complete',attemptId:'attempt',request:[{role:'user',content:'写一章'}],providerRequestId:'first-request',finishReason:'stop',usage:{input:30,output:20},warnings:[]}],status:'completed',ownerId:'owner',leaseUntil:null,acceptedCount:1,acceptedRevision:1,historical:false,createdAt:'2026-10-06',updatedAt:'2026-10-06',error:null}
 const branch:LocalBranch={storyId:'book',name:'另一条线',archived:false,parentBranchId:null,parentRevision:1,storyRevision:1,fork:anchor,prefixChapters:prefix,chapters:[...prefix,{id:'c2',title:'来信',paragraphIds:['p2','p3']}],paragraphIds:['p1','p2','p3'],createdAt:'2026-10-06'}
 const rows:Record<string,{kind:'settings'|'story'|'paragraph'|'branch';revision:number;value:unknown}>={
  [profile.id]:{kind:'settings',revision:1,value:profile},book:{kind:'story',revision:1,value:{chapters:prefix}},p1:{kind:'paragraph',revision:1,value:{chapterId:'c1',text:'起点'}},
  p2:{kind:'paragraph',revision:1,value:{id:'p2',chapterId:'c2',text:'来信仍在旅人手中。',sourceStart:0,sourceEnd:9,writingRunId:id}},
  p3:{kind:'paragraph',revision:1,value:{id:'p3',chapterId:'c2',text:'他走到钟楼。',sourceStart:11,sourceEnd:17,writingRunId:id}},[id]:{kind:'settings',revision:1,value:run},branch:{kind:'branch',revision:1,value:branch}
 }
 return {run,branch,rows,lookup:(key:string,rev?:number)=>{const row=rows[key];return row&&(!rev||row.revision===rev)?row:undefined}}
}
it('accepts only the entire ordered chapter, rejecting a substring, omission, changed offset and target title',()=>{
 const f=fixture();expect(()=>validateWritingRun(f.run)).not.toThrow();expect(()=>validateLocalRelations('settings',id,f.run,f.lookup)).not.toThrow()
 for(const patch of [{text:'来信',sourceEnd:2},{sourceStart:13},{text:'另一段'}]){const g=fixture();g.rows.p2!.value={...g.rows.p2!.value as object,...patch};expect(()=>validateLocalRelations('settings',id,g.run,g.lookup)).toThrow()}
 const short=fixture();short.run.chapters[0]!.paragraphIds=['p2'];short.branch.chapters[1]!.paragraphIds=['p2'];expect(()=>validateLocalRelations('settings',id,short.run,short.lookup)).toThrow()
 const title=fixture();title.branch.chapters[1]!.title='伪造标题';expect(()=>validateLocalRelations('settings',id,title.run,title.lookup)).toThrow()
})
it('freezes known request IDs, finish, usage and complete or partial streams after termination',()=>{
 const f=fixture(),before={id,kind:'settings' as const,revision:1,updatedAt:'2026-10-06',value:f.run}
 for(const patch of [{providerRequestId:'changed'},{finishReason:'end_turn'},{usage:{input:999,output:999}},{usage:{input:null,output:null}},{text:f.run.chapters[0]!.text+'晚到正文'}]){const value=JSON.parse(canonical(f.run));Object.assign(value.chapters[0],patch);expect(()=>validateUpdate('settings',before,value)).toThrow()}
 const partial=fixture();partial.run.status='cancelled';partial.run.acceptedCount=0;partial.run.acceptedRevision=0;partial.run.chapters[0]!.status='partial';partial.run.chapters[0]!.paragraphIds=[];const changed=JSON.parse(canonical(partial.run));changed.chapters[0].text+='伪造晚到';expect(()=>validateUpdate('settings',{...before,value:partial.run},changed)).toThrow()
})
it('refuses forged historical profiles, endpoint, model, allowances and timeouts but preserves exact frozen settings',()=>{
 expect(()=>validateWritingModel(config,timeouts,3,profile,1,false)).not.toThrow()
 for(const patch of [{id:'model:foreign'},{revision:999},{baseUrl:'https://elsewhere.invalid/v1'},{modelId:'foreign'}])expect(()=>validateWritingModel({...config,...patch},timeouts,3,profile,1,false)).toThrow()
 expect(()=>validateWritingModel(config,{...timeouts,idleMs:2000},3,profile,1,false)).toThrow();expect(()=>validateWritingModel(config,timeouts,30,profile,1,false)).toThrow()
 const restored={...profile,key:'',enabled:false};expect(()=>validateWritingModel({...config,key:''},timeouts,3,restored,1,true)).not.toThrow()
})
it('refuses credential echoes in readable or original provider output and request identity',()=>{
 for(const field of ['text','raw','providerRequestId','finishReason','title']){const f=fixture();(f.run.chapters[0] as unknown as Record<string,unknown>)[field]=profile.key;expect(()=>validateWritingRun(f.run)).toThrow()}
})
it('verifies genuine migrated stream evolution by its recorded original phase while freezing live historical edits',()=>{
 const run=fixture().run;run.historical=true;run.status='unknown';run.originalStatus='running';run.acceptedCount=0;run.acceptedRevision=0;run.chapters[0]!.paragraphIds=[];run.chapters[0]!.status='partial';run.chapters[0]!.originalStatus='running';run.chapters[0]!.text='来信';run.chapters[0]!.usage={input:null,output:null};run.chapters[0]!.finishReason=null
 const next=JSON.parse(canonical(run)) as WritingRun;next.chapters[0]!.text='来信仍在旅人手中。';next.chapters[0]!.status='complete';next.chapters[0]!.originalStatus='complete';next.chapters[0]!.finishReason='stop';next.chapters[0]!.usage={input:30,output:20}
 const previous={id,kind:'settings' as const,revision:1,updatedAt:'2026-10-06',value:run}
 expect(()=>validateWritingRun(run)).not.toThrow();expect(()=>validateWritingRun(next)).not.toThrow()
 expect(()=>validateUpdate('settings',{...previous,value:originalWritingPhases(run)},originalWritingPhases(next))).not.toThrow()
 expect(()=>validateUpdate('settings',previous,next)).toThrow()
 const terminal={...next,status:'completed' as const,originalStatus:'completed' as const},late=JSON.parse(canonical(terminal));late.chapters[0].text+='伪造晚到';expect(()=>validateUpdate('settings',{...previous,value:originalWritingPhases(terminal)},originalWritingPhases(late))).toThrow()
})
