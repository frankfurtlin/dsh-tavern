import {describe,it,expect} from 'vitest'
import {renderedOffset,canonicalOffset,textBoundaries} from './reading-layout'
describe('logical reader positions survive DOM whitespace and emoji',()=>{
  it('keeps exact UTF-16 boundaries, including the middle of a long paragraph',()=>{
    const text='旧信😀'+('渡口来信'.repeat(100))
    for(const offset of [0,2,4,305,text.length])expect(renderedOffset(text,text,offset)).toBe(offset)
    expect(()=>renderedOffset(text,text,3)).toThrow('ANCHOR_INVALID')
  })
  it('maps inline markup whitespace without counting it as story content',()=>{
    expect(renderedOffset('甲😀乙','甲\n 😀\n乙',3)).toBe(6)
    expect(canonicalOffset('甲😀乙','甲\n 😀\n乙',6)).toBe(3)
    expect(textBoundaries('甲😀乙')).toEqual([0,1,3,4])
  })
  it('rejects a different revision rather than guessing a nearby paragraph offset',()=>{
    expect(()=>renderedOffset('甲😀乙','甲😀丙',3)).toThrow()
    expect(()=>renderedOffset('渡口','渡口',1.5)).toThrow()
  })
})
