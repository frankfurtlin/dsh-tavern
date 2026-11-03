import {describe,expect,it,vi} from 'vitest'
vi.mock('../api',()=>({api:vi.fn()}))
vi.mock('../platform/request-scope',()=>({useRequestScope:vi.fn()}))
import {curatedCover} from './curated-artwork'

describe('curated story artwork',()=>{
 it('keeps the existing covers and supplies a distinct Douluo cover',()=>{
  expect(curatedCover('blue-carbuncle')).toBe('/assets/covers/blue-carbuncle-v1.jpg')
  expect(curatedCover('original-mystery-v1')).toBe('/assets/covers/clocktower-promise-v1.jpg')
  expect(curatedCover('original-fantasy-v1')).toBe('/assets/covers/snowpass-companions-v1.jpg')
  expect(curatedCover('douluo-forest-v1')).toBe('/assets/covers/douluo-forest-v1.svg')
 })
 it('does not infer a cover from an arbitrary user title or inherited key',()=>{
  expect(curatedCover('斗罗大陆·星斗试炼')).toBeNull()
  expect(curatedCover('constructor')).toBeNull()
  expect(curatedCover(undefined)).toBeNull()
 })
})
