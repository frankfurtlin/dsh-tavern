import type {InstalledPlugin,PluginManifest,PluginInput,PluginOutput} from './contract'
const defaults={schema:'reader-plugin-v1' as const,version:'1.0.0',modelCalls:0 as const,budget:{steps:1,milliseconds:300,bytes:32000},parameters:{maximum:6}}
const definitions:Omit<PluginManifest,keyof typeof defaults>[]=[
 {id:'long-facts',name:'长期事实',description:'引用明确保存的记忆；保留原文与来源。',hooks:['beforePrompt'],reads:['memory'],writes:['context']},
 {id:'history-recall',name:'历史回顾',description:'按当前问题查找选中、可见的历史消息。',hooks:['beforePrompt','onUserAction'],reads:['messages'],writes:['context','suggestions']},
 {id:'memory-library',name:'记忆库',description:'查看已确认记忆，提供引用清单。',hooks:['onUserAction'],reads:['memory'],writes:['suggestions']},
 {id:'history-summary',name:'近期摘要',description:'从选中消息生成带来源的节选；不会代替完整历史。',hooks:['beforePrompt','afterCommit'],reads:['messages'],writes:['context','suggestions']},
 {id:'reply-inspiration',name:'回复灵感',description:'给出可编辑的回复方向，点击后填入草稿。',hooks:['onUserAction'],reads:['messages','card'],writes:['suggestions']},
 {id:'story-directions',name:'剧情方向',description:'从当前场景提供三种不同的继续方式。',hooks:['onUserAction'],reads:['messages','card'],writes:['suggestions']},
 {id:'state-tracker',name:'状态追踪',description:'提取明确的时间、地点、行动句，保留证据。',hooks:['afterText','onUserAction'],reads:['messages'],writes:['suggestions']},
 {id:'lore-suggestions',name:'世界书建议',description:'从已有世界书条目查找相关设定，预览后使用。',hooks:['onUserAction'],reads:['lorebook','messages'],writes:['suggestions']},
 {id:'candidate-guard',name:'候选保护',description:'重新生成前要求确认，保留所有已有候选。',hooks:['onUserAction'],reads:[],writes:['confirmation']},
 {id:'paragraph-display',name:'段落整理',description:'只整理显示层的多余空行，原消息不变。',hooks:['beforeRender'],reads:[],writes:['displayText']}
]
export const builtinPlugins:InstalledPlugin[]=definitions.map(manifest=>({assetType:'chat-plugin',manifest:{...defaults,...manifest},source:'',builtin:manifest.id,enabled:false,grants:[],deleted:false,createdAt:'2026-10-06T00:00:00.000Z'}))
function maximum(parameters:Record<string,string|number|boolean>){return Math.min(8,Math.max(1,Number.isSafeInteger(parameters.maximum)?Number(parameters.maximum):6))}
function ranked(input:PluginInput,kind?:string){const terms=input.query.toLocaleLowerCase().match(/[\p{L}\p{N}]{2,}|[\u4e00-\u9fff]/gu)||[];return input.references.filter(ref=>!kind||ref.kind===kind).map((ref,index)=>({ref,index,score:terms.reduce((n,term)=>n+(ref.text.toLocaleLowerCase().includes(term)?1:0),0)})).sort((a,b)=>b.score-a.score||b.index-a.index).map(row=>row.ref)}
export function executeBuiltin(id:string,input:PluginInput,parameters:Record<string,string|number|boolean>):PluginOutput{
 const max=maximum(parameters),sources=(kind?:string)=>ranked(input,kind).slice(0,max),suggestions=(kind?:string)=>sources(kind).map(ref=>({title:ref.title,text:ref.text.slice(0,1500),sourceIds:[ref.id]})),context=(kind?:string)=>sources(kind).map(ref=>'['+ref.id+' · 版本 '+ref.revision+'] '+ref.text.slice(0,1500)).join('\n\n')
 switch(id){
  case 'long-facts':return {context:context('memory')}
  case 'history-recall':return input.hook==='beforePrompt'?{context:'历史检索节选（仅所选可见消息）：\n'+context('messages')}:{suggestions:suggestions('messages')}
  case 'memory-library':return {suggestions:suggestions('memory')}
  case 'history-summary':{const refs=input.references.filter(ref=>ref.kind==='messages').slice(-max),rows=refs.map(ref=>({title:ref.title,text:ref.text.slice(0,240),sourceIds:[ref.id]}));return input.hook==='beforePrompt'?{context:'近期节选（不是完整历史摘要）：\n'+rows.map(row=>'['+row.sourceIds[0]+'] '+row.text).join('\n')}:{suggestions:rows}}
  case 'reply-inspiration':{const last=input.references.filter(ref=>ref.kind==='messages').at(-1),sourceIds=last?[last.id]:[];return {suggestions:[{title:'追问细节',text:'你刚才提到的这件事，能再多告诉我一点吗？',sourceIds},{title:'表达感受',text:'听你这样说，我也想和你分享我的感受。',sourceIds},{title:'留出选择',text:'接下来你想怎么做？我想听听你的想法。',sourceIds}]}}
  case 'story-directions':{const ref=input.references.filter(ref=>ref.kind==='messages').at(-1),sourceIds=ref?[ref.id]:[];return {suggestions:[{title:'走向外部',text:'让角色离开当前场景，主动寻找眼前问题的线索。',sourceIds},{title:'靠近人物',text:'让角色在此刻坦诚一次，把注意力放在彼此的关系。',sourceIds},{title:'改变节奏',text:'安排一个与当前线索相关的小意外，暂时打断原计划。',sourceIds}]}}
  case 'state-tracker':{const refs=input.references.filter(ref=>ref.kind==='messages'),current=input.hook==='afterText'&&input.text?[{id:'',title:'本次回复',text:input.text}]:[],rows=[...refs,...current].flatMap(ref=>ref.text.split(/(?<=[。！？\n])/u).filter(text=>/(?:来到|离开|站在|坐在|走进|时间|清晨|夜晚|下午|这里|那里|在.{1,16}(?:街|港|屋|房|站|学校|岸))/.test(text)).map(text=>({title:ref.title,text:text.trim().slice(0,500),sourceIds:ref.id?[ref.id]:[]}))).slice(-max);return {suggestions:rows,diagnostics:['所列是原句节选，未自动改变事实；本次回复来源见关联请求。']}}
  case 'lore-suggestions':return {suggestions:suggestions('lorebook')}
  case 'candidate-guard':return input.query==='regenerate'&&input.text?{confirmation:'保留这条回复，再生成一个新候选？'}:{}
  case 'paragraph-display':return {displayText:input.text.replace(/\n{3,}/g,'\n\n')}
  default:throw new Error('未知内置插件')
 }
}
