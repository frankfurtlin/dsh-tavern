import {afterEach,beforeEach,it,expect,vi} from 'vitest'
import {DEFAULT_READING_PREFERENCE,READING_PREFERENCE_KEY,loadReadingPreference,normalizeReadingPreference,readingPreference,readingStyle,resetReadingPreference,updateReadingPreference} from './reading-preference'

function storageStub(initial:Record<string,string>={}){
  const data={...initial}
  return{
    data,
    getItem:(key:string)=>data[key]??null,
    setItem:(key:string,value:string)=>{data[key]=value},
    removeItem:(key:string)=>{delete data[key]},
  }
}

beforeEach(()=>{
  vi.stubGlobal('localStorage',storageStub())
})
afterEach(()=>{
  vi.unstubAllGlobals()
})

it('clamps out-of-range values into the supported reading range',()=>{
  expect(normalizeReadingPreference({fontSize:99,lineHeight:0.2,fontFamily:'serif'})).toEqual({fontSize:26,lineHeight:1.4,fontFamily:'serif'})
  expect(normalizeReadingPreference({fontSize:1,lineHeight:9,fontFamily:'sans'})).toEqual({fontSize:14,lineHeight:2.4,fontFamily:'sans'})
})

it('falls back to the default preference when storage is empty or broken',()=>{
  expect(loadReadingPreference()).toEqual(DEFAULT_READING_PREFERENCE)
  vi.stubGlobal('localStorage',storageStub({[READING_PREFERENCE_KEY]:'{oops'}))
  expect(loadReadingPreference()).toEqual(DEFAULT_READING_PREFERENCE)
})

it('persists every change to localStorage and exposes px styles for the article body',()=>{
  expect(readingPreference.value).toEqual(DEFAULT_READING_PREFERENCE)
  updateReadingPreference({fontSize:20,lineHeight:2.1,fontFamily:'serif'})
  const stored=JSON.parse(localStorage.getItem(READING_PREFERENCE_KEY)||'{}')
  expect(stored).toMatchObject({fontSize:20,lineHeight:2.1,fontFamily:'serif'})
  expect(readingPreference.value.fontSize).toBe(20)
  expect(readingStyle.value.fontSize).toBe('20px')
  expect(readingStyle.value.lineHeight).toBe('2.1')
  expect(String(readingStyle.value.fontFamily)).toContain('serif')
})

it('resets back to the documented defaults',()=>{
  updateReadingPreference({fontSize:24})
  resetReadingPreference()
  expect(readingPreference.value).toEqual(DEFAULT_READING_PREFERENCE)
  expect(JSON.parse(localStorage.getItem(READING_PREFERENCE_KEY)||'{}')).toEqual(DEFAULT_READING_PREFERENCE)
})
