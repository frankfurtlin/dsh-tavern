import {it,expect,vi} from 'vitest'
import {parseOriginalPack,copyOriginalAsset,type OriginalAsset} from './original-pack'
import {sha256} from '../domain/local-contract'
import type {LocalVault} from '../repositories/local-vault'
const asset=():OriginalAsset=>({id:'final-person-own',name:'成年原创人物',purpose:'stage-portrait',expression:'neutral',masterHash:'a'.repeat(64),variants:[{role:'standing-960',url:'/assets/original/final-product/own-neutral-standing-960.webp',width:640,height:960,bytes:4,sha256:'b'.repeat(64)}]})
it('copies only the public catalogue shape and never carries private extra fields',()=>{
 const input={schema:'original-pack-v1',version:1,assets:[{...asset(),PRIVATE:'do not copy'}],PRIVATE:'do not copy'}
 expect(JSON.stringify(parseOriginalPack(input))).not.toContain('PRIVATE');expect(input.assets[0].PRIVATE).toBe('do not copy')
})
it.each(['external','traversal','encoded','duplicate-role','huge','duplicate-asset'])('rejects unsafe original catalogue %s',fault=>{
 const a=asset(),input={schema:'original-pack-v1',version:1,assets:[a]}
 if(fault==='external')a.variants[0].url='https://provider.invalid/private.png'
 if(fault==='traversal')a.variants[0].url='/assets/original/final-product/../../private.png'
 if(fault==='encoded')a.variants[0].url='/assets/original/final-product/%2e%2e.png'
 if(fault==='duplicate-role')a.variants.push({...a.variants[0]})
 if(fault==='huge')a.variants[0].bytes=26*1024*1024
 if(fault==='duplicate-asset')input.assets.push(structuredClone(a))
 expect(()=>parseOriginalPack(input)).toThrow()
})
it('existing exact imported display copy returns without a fetch or mutation',async()=>{
 const a=asset(),existing={id:'owned',value:{sha256:a.variants[0].sha256,bytes:4,width:640,height:960}},transport=vi.fn(),commit=vi.fn(),vault={list:async()=>[existing],commitBundle:commit} as unknown as LocalVault
 expect(await copyOriginalAsset(vault,a,'standing-960',new AbortController().signal,transport)).toBe(existing);expect(transport).not.toHaveBeenCalled();expect(commit).not.toHaveBeenCalled()
})
it.each(['oversized-body','wrong-hash','cancelled'])('bad or cancelled file %s writes nothing',async fault=>{
 const a=asset(),commit=vi.fn(),vault={list:async()=>[],unlocked:true,commitBundle:commit} as unknown as LocalVault,control=new AbortController(),transport=vi.fn(async()=>new Response(new Uint8Array(fault==='oversized-body'?[1,2,3,4,5]:[1,2,3,4])))
 if(fault==='cancelled')control.abort()
 await expect(copyOriginalAsset(vault,a,'standing-960',control.signal,transport)).rejects.toThrow();expect(commit).not.toHaveBeenCalled();if(fault==='cancelled')expect(transport).not.toHaveBeenCalled()
})
it('verified WebP bytes with false dimensions are rejected before any asset write',async()=>{
 const a=asset(),bytes=new Uint8Array([82,73,70,70,0,0,0,0,87,69,66,80]);a.variants[0].bytes=bytes.length;a.variants[0].sha256=await sha256(bytes)
 const commit=vi.fn(),vault={list:async()=>[],unlocked:true,commitBundle:commit} as unknown as LocalVault,close=vi.fn()
 vi.stubGlobal('createImageBitmap',async()=>({width:128,height:128,close}))
 try{await expect(copyOriginalAsset(vault,a,'standing-960',new AbortController().signal,async()=>new Response(bytes))).rejects.toThrow('尺寸');expect(commit).not.toHaveBeenCalled();expect(close).toHaveBeenCalled()}finally{vi.unstubAllGlobals()}
})
