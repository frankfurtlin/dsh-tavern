import * as vue from 'vue'
import {createRenderer,ssrContextKey,type Component} from 'vue'
import {parse,compileScript,compileTemplate} from '@vue/compiler-sfc'
import {readFileSync} from 'node:fs'
import ts from 'typescript'

// Run the actual SFC template and handlers without substituting component logic.
// Geometry, native speech and browser pagination require the separate real DOM suite.
export function compileRender(component:Component,url:URL){
  const descriptor=parse(readFileSync(url,'utf8')).descriptor
  const template=compileTemplate({source:descriptor.template!.content,filename:url.pathname,id:'next-unit',compilerOptions:{bindingMetadata:compileScript(descriptor,{id:'next-unit'}).bindings}})
  if(template.errors.length)throw new Error(String(template.errors[0]))
  const source=ts.transpileModule(template.code,{compilerOptions:{module:ts.ModuleKind.CommonJS,target:ts.ScriptTarget.ES2022}}).outputText
  const exports:{render?:Function}={}
  new Function('require','exports',source)((name:string)=>{if(name==='vue')return vue;throw new Error('Unexpected template import '+name)},exports)
  Object.assign(component,{render:exports.render})
}
export type Host={tag:string;text:string;children:Host[];parent:Host|null;props:Record<string,any>;value:string;addEventListener:()=>void;removeEventListener:()=>void}
export const node=(tag:string,text=''):Host=>{
  const host:Host={tag,text,children:[],parent:null,props:{},value:'',addEventListener:()=>{},removeEventListener:()=>{}}
  // Minimal form surface for Vue's real v-model; no browser layout is simulated.
  Object.defineProperty(host,'tagName',{value:tag.toUpperCase()})
  Object.defineProperty(host,'options',{get:()=>host.children.filter(n=>n.tag==='option')})
  return host
}
export const descendants=(root:Host,match:(node:Host)=>boolean):Host[]=>[...(match(root)?[root]:[]),...root.children.flatMap(child=>descendants(child,match))]
export const text=(root:Host):string=>(root.tag==='#comment'?'':root.text)+root.children.map(text).join('')
export const renderer=createRenderer<Host,Host>({
  createElement:tag=>node(tag),createText:value=>node('#text',value),createComment:value=>node('#comment',value),
  setText:(element,value)=>{element.text=value},setElementText:(element,value)=>{element.text=value;element.children=[]},patchProp:(element,key,_old,value)=>{element.props[key]=value;if(key==='value'){element.value=String(value);(element as Host&{_value:unknown})._value=value}},
  insert:(element,parent,anchor)=>{if(element.parent){const old=element.parent.children.indexOf(element);if(old>=0)element.parent.children.splice(old,1)}element.parent=parent;const position=anchor?parent.children.indexOf(anchor):-1;if(position<0)parent.children.push(element);else parent.children.splice(position,0,element)},
  remove:element=>{if(element.parent){const position=element.parent.children.indexOf(element);if(position>=0)element.parent.children.splice(position,1)}element.parent=null},
  parentNode:element=>element.parent,nextSibling:element=>element.parent?.children[element.parent.children.indexOf(element)+1]||null
})
export function provideSfc(app:ReturnType<typeof renderer.createApp>){app.provide(ssrContextKey,{modules:new Set()})}
export const flush=async()=>{for(let i=0;i<45;i++)await Promise.resolve()}
export function deferred<T>(){let resolve!:(value:T)=>void,reject!:(error:Error)=>void;const promise=new Promise<T>((a,b)=>{resolve=a;reject=b});return{promise,resolve,reject}}
