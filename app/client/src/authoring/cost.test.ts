import {describe,it,expect} from 'vitest'
import {authorCost,tokenRange} from './cost'
import type {AuthorValue} from './types'
const value=()=>({authorInstructions:'只观察，不交接。',definition:{description:'雨中的书店',narrativeStyle:'克制',entities:[{type:'character',name:'店主',description:'整理旧书',traits:'平静',hiddenGoal:'保密'}],lore:[{title:'秘密',text:'私密口令',triggers:['询问']}]},rules:{},playerFields:[],statusFields:[],openings:[{id:'one',text:'看一眼'},{id:'two',text:'来到书店，先和店主打招呼。'}]} as unknown as AuthorValue)
describe('author cost disclosure',()=>{
 it('keeps conditional lore separate and counts only the chosen opening',()=>{const a=authorCost(value(),'one'),b=authorCost(value(),'two');expect(a.find(r=>r.id==='lore')?.conditional).toBe(true);expect(a.find(r=>r.id==='people')?.conditional).toBe(true);expect(b.find(r=>r.id==='opening')!.characters).toBeGreaterThan(a.find(r=>r.id==='opening')!.characters)})
 it('does not treat embedded image bytes as prose tokens',()=>{const v=value();v.statusFields=[{id:'author_face',label:'头像',type:'image',defaultValue:'data:image/png;base64,'+'A'.repeat(100000),options:[],required:false,minimum:null,maximum:null,scope:'player'}];const image=authorCost(v).find(r=>r.id==='fields')!;expect(image.characters).toBeLessThan(1000);expect(tokenRange('中文 hello').low).toBeLessThan(tokenRange('中文 hello').high)})
})
