import type {AuthorValue} from './types'
// A visible range, not a tokenizer or a monetary quotation. Never count a
// data URL as story text, and keep triggered lore separate from the core.
export function tokenRange(text:string){const clean=text.replace(/data:image\/[a-z]+;base64,[A-Za-z0-9+/=]+/g,'[图片]'),cjk=(clean.match(/[\u3400-\u9fff]/g)||[]).length,other=clean.length-cjk;return {characters:clean.length,low:Math.ceil(cjk*.7+other/5),high:Math.ceil(cjk*1.5+other/2)}}
export function authorCost(value:AuthorValue,openingId?:string){
 const fields=[...value.playerFields,...value.statusFields].map(f=>JSON.stringify({...f,defaultValue:f.type==='image'?'[图片]':f.defaultValue})).join('\n')
 const parts=[{id:'core',title:'核心设定与规则',text:[value.authorInstructions,value.definition.description,value.definition.narrativeStyle,JSON.stringify(value.rules),JSON.stringify(value.plot||{})].join('\n'),conditional:false},
 {id:'opening',title:'当前开局',text:(value.openings.find(o=>o.id===openingId)||value.openings[0])?.text||'',conditional:false},
 {id:'fields',title:'开局与状态字段',text:fields,conditional:false},
 {id:'people',title:'人物设定（按场景选入）',text:value.definition.entities.filter(e=>e.type==='character').map(e=>[e.name,e.description,e.traits,e.hiddenGoal].join('\n')).join('\n'),conditional:true},
 {id:'lore',title:'知识设定（按触发选入）',text:value.definition.lore.map(l=>[l.title,l.text,...l.triggers].join('\n')).join('\n'),conditional:true}]
 return parts.map(({text,...p})=>({...p,...tokenRange(text)}))
}
