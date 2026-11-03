// Execute real setup/template handlers without claiming browser layout or native IO.
import * as vue from 'vue'
import {createRenderer,defineComponent,h,ssrContextKey,type Component} from 'vue'
import {parse,compileScript,compileTemplate} from '@vue/compiler-sfc'
import {readFileSync} from 'node:fs'
import ts from 'typescript'
export type Host={tag:string;text:string;children:Host[];parent:Host|null;props:Record<string,any>}
function node(tag:string,text=''):Host{return {tag,text,children:[],parent:null,props:{}}}
export const hostText=(n:Host):string=>(n.tag==='#comment'?'':n.text)+n.children.map(hostText).join('')
export function hostAll(n:Host,test:(n:Host)=>boolean):Host[]{return [...(test(n)?[n]:[]),...n.children.flatMap(c=>hostAll(c,test))]}
const renderer=createRenderer<Host,Host>({
  createElement:tag=>node(tag),createText:text=>node('#text',text),createComment:text=>node('#comment',text),
  setText:(n,text)=>{n.text=text},setElementText:(n,text)=>{n.text=text;n.children=[]},patchProp:(n,key,_old,value)=>{n.props[key]=value},
  insert:(n,parent,anchor)=>{if(n.parent){const i=n.parent.children.indexOf(n);if(i>=0)n.parent.children.splice(i,1)}n.parent=parent;const i=anchor?parent.children.indexOf(anchor):-1;if(i<0)parent.children.push(n);else parent.children.splice(i,0,n)},
  remove:n=>{if(n.parent){const i=n.parent.children.indexOf(n);if(i>=0)n.parent.children.splice(i,1)}n.parent=null},
  parentNode:n=>n.parent,nextSibling:n=>n.parent?.children[n.parent.children.indexOf(n)+1]||null
})
export function compileActualTemplate(component:Component,filename:URL){
  const descriptor=parse(readFileSync(filename,'utf8')).descriptor,id='world-offline-unit'
  const template=compileTemplate({source:descriptor.template!.content,filename:filename.pathname,id,compilerOptions:{bindingMetadata:compileScript(descriptor,{id}).bindings}})
  if(template.errors.length)throw new Error(String(template.errors[0]))
  const compiled=ts.transpileModule(template.code,{compilerOptions:{module:ts.ModuleKind.CommonJS,target:ts.ScriptTarget.ES2022}}).outputText,exports:{render?:Function}={}
  new Function('require','exports',compiled)((name:string)=>{if(name==='vue')return vue;throw new Error('Unexpected template dependency: '+name)},exports)
  Object.assign(component,{render:exports.render})
}
export function mountHost(component:Component,props:Record<string,unknown>={}){
  const root=node('root'),app=renderer.createApp(defineComponent({setup:()=>()=>h(component,props)}))
  app.provide(ssrContextKey,{modules:new Set()})
  app.component('RouterLink',defineComponent({props:{to:[String,Object]},setup:(p,{slots})=>()=>h('a',{href:p.to},slots.default?.())}))
  app.mount(root)
  return {root,app,find:(test:(n:Host)=>boolean)=>hostAll(root,test)[0]}
}
export const flushHost=async()=>{for(let i=0;i<50;i++)await Promise.resolve()}
export const hostDeferred=<T>()=>{let resolve!:(v:T)=>void,reject!:(e:Error)=>void;const promise=new Promise<T>((a,b)=>{resolve=a;reject=b});return {promise,resolve,reject}}
