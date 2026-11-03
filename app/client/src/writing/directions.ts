import {ContractError} from '../domain/local-contract'
import type {ProsePresentation} from './prose'
export interface StoryDirection {title:string;approach:'investigate'|'escape'|'negotiate'|'confront'|'protect'|'sacrifice'|'reconcile';decision:string;conflict:string;consequence:string;direction:string}
const approaches=['investigate','escape','negotiate','confront','protect','sacrifice','reconcile']
export function parseStoryDirections(raw:string):StoryDirection[]{
 const fail=():never=>{throw new ContractError('WRITING_DIRECTIONS','这次方向未形成三条独立方案；原返回保留，可自由填写走向')}
 let value:unknown;try{value=JSON.parse(raw.trim().replace(/^```json\s*/i,'').replace(/\s*```$/,''))}catch{fail()}
 if(!value||typeof value!=='object'||Array.isArray(value)||Object.keys(value).some(k=>k!=='choices')||!Array.isArray((value as {choices:unknown}).choices))fail()
 const choices=(value as {choices:StoryDirection[]}).choices
 if(choices.length!==3)fail()
 for(const choice of choices){if(!choice||typeof choice!=='object'||Array.isArray(choice)||Object.keys(choice).some(k=>!['title','approach','decision','conflict','consequence','direction'].includes(k))||!approaches.includes(choice.approach)||['title','decision','conflict','consequence','direction'].some(field=>typeof choice[field as keyof StoryDirection]!=='string'||!choice[field as keyof StoryDirection].trim()||choice[field as keyof StoryDirection].length>(field==='title'?100:2000)))fail()}
 for(const field of ['title','approach','decision','direction'] as const)if(new Set(choices.map(c=>c[field].replace(/\s|[，。！？、,.!?]/g,'').toLocaleLowerCase())).size!==3)fail()
 return choices
}
export function directionPresentation(raw:string):ProsePresentation{
 try{const choices=parseStoryDirections(raw);return {text:choices.map((c,i)=>(i+1)+'. '+c.title+'\n'+c.direction).join('\n\n'),title:null,format:'json',thoughtOmitted:false,pending:false}}catch{return {text:'',title:null,format:'unrecognized',thoughtOmitted:false,pending:true}}
}
export const directionSystemPrompt='根据已读上下文规划三条实质不同的下一步剧情。每条必须在主角决定、阻力与后果上有区别，不是替换角色名或同一事件换词；不要读点之后的原文，不把推测当已发生。只返回JSON {"choices":[{"title":"短标题","approach":"investigate|escape|negotiate|confront|protect|sacrifice|reconcile 之一，三条互不重复","decision":"主角接下来做什么","conflict":"面临的阻力","consequence":"可能造成的变化","direction":"可直接用于后续写作的完整走向"}]}，恰好三条。'
