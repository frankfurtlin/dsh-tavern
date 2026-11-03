import {it,expect} from 'vitest'
import {createHash} from 'node:crypto'
import {ByteSha256} from './stream-hash'
it('matches standard SHA-256 vectors with fragmented padding and UTF-8 downloads',()=>{
  for(const source of ['', 'abc','a'.repeat(55),'b'.repeat(56),'c'.repeat(64),'a'.repeat(1000000),'甲😀乙\n'.repeat(500)]){
    const bytes=new TextEncoder().encode(source),digest=new ByteSha256()
    for(let i=0;i<bytes.length;i+=17)digest.update(bytes.subarray(i,i+17))
    expect(digest.hex()).toBe(createHash('sha256').update(bytes).digest('hex'))
    expect(()=>digest.update(new Uint8Array())).toThrow();expect(()=>digest.hex()).toThrow()
  }
})
