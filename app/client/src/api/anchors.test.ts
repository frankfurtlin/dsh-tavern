import {describe,it,expect} from 'vitest'
import {quoteHash} from './anchors'
import vectors from './anchor-vectors.json'
describe('F03 UTF-16 anchors',()=>{
  for(const vector of vectors)it('shared vector '+vector.text.slice(0,12)+' '+vector.start,async()=>{expect(await quoteHash(vector.text,vector.revisionId,vector.blockId,vector.start,vector.end)).toBe(vector.hash)})
  it('rejects an offset inside an emoji surrogate pair',async()=>{await expect(quoteHash('甲😀乙','r','b',2)).rejects.toThrow('ANCHOR_INVALID')})
  it('uses version, block and boundary context for zero-length hashes',async()=>{expect(await quoteHash('甲😀乙','r','b',3)).not.toBe(await quoteHash('甲😀乙','r2','b',3));expect(await quoteHash('甲😀乙','r','b',0)).not.toBe(await quoteHash('甲😀乙','r','b',3))})
  it('matches a SHA256 selection vector',async()=>{expect(await quoteHash('abc','r','b',0,3)).toBe('ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad')})
})
