import {computed,ref} from 'vue'

// P1 阅读感：字号 / 行高 / 字体只保存在本机，不写入服务端阅读设置。
// 固定浅色主题，因此这里只处理排版偏好。
export type ReadingFontFamily='sans'|'serif'

export interface ReadingPreference{
  fontSize:number
  lineHeight:number
  fontFamily:ReadingFontFamily
}

export const READING_PREFERENCE_KEY='tavern:reading-preference'
export const DEFAULT_READING_PREFERENCE:ReadingPreference={fontSize:16,lineHeight:1.9,fontFamily:'sans'}

export const READING_FONT_STACKS:Record<ReadingFontFamily,string>={
  sans:'"Noto Sans SC","PingFang SC","Microsoft YaHei",system-ui,sans-serif',
  serif:'"Noto Serif SC","Songti SC","SimSun",Georgia,serif',
}

const MIN_FONT_SIZE=14,MAX_FONT_SIZE=26,MIN_LINE_HEIGHT=1.4,MAX_LINE_HEIGHT=2.4

function clamp(value:number,min:number,max:number,fallback:number){
  return Number.isFinite(value)?Math.min(max,Math.max(min,value)):fallback
}

export function normalizeReadingPreference(value:Partial<ReadingPreference>|null|undefined):ReadingPreference{
  const source=value||{}
  const fontFamily:ReadingFontFamily=source.fontFamily==='serif'?'serif':'sans'
  return{
    fontSize:clamp(Number(source.fontSize),MIN_FONT_SIZE,MAX_FONT_SIZE,DEFAULT_READING_PREFERENCE.fontSize),
    lineHeight:clamp(Number(source.lineHeight),MIN_LINE_HEIGHT,MAX_LINE_HEIGHT,DEFAULT_READING_PREFERENCE.lineHeight),
    fontFamily,
  }
}

export function loadReadingPreference():ReadingPreference{
  try{
    if(typeof localStorage==='undefined')return{...DEFAULT_READING_PREFERENCE}
    const raw=localStorage.getItem(READING_PREFERENCE_KEY)
    if(!raw)return{...DEFAULT_READING_PREFERENCE}
    return normalizeReadingPreference(JSON.parse(raw) as Partial<ReadingPreference>)
  }catch{
    return{...DEFAULT_READING_PREFERENCE}
  }
}

export function saveReadingPreference(value:ReadingPreference){
  try{
    if(typeof localStorage!=='undefined')localStorage.setItem(READING_PREFERENCE_KEY,JSON.stringify(value))
  }catch{/* 隐私模式或配额不足时保持内存值 */ }
  return value
}

// 模块级单例：卡库、聊天页与阅读页共享同一份偏好。
export const readingPreference=ref<ReadingPreference>(loadReadingPreference())

export function updateReadingPreference(patch:Partial<ReadingPreference>):ReadingPreference{
  readingPreference.value=normalizeReadingPreference({...readingPreference.value,...patch})
  return saveReadingPreference(readingPreference.value)
}

export function resetReadingPreference():ReadingPreference{
  readingPreference.value={...DEFAULT_READING_PREFERENCE}
  return saveReadingPreference(readingPreference.value)
}

export const readingStyle=computed(()=>({
  fontSize:readingPreference.value.fontSize+'px',
  lineHeight:String(readingPreference.value.lineHeight),
  fontFamily:READING_FONT_STACKS[readingPreference.value.fontFamily],
}))
