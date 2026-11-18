export const GUIDE_SEEN_KEY='tavern:guide-seen'
export const HINT_DISMISSED_KEY='tavern:hint-dismissed'

function storage():Storage|null{
  try{
    if(typeof window==='undefined')return null
    return window.localStorage
  }catch{return null}
}

function read(key:string):string|null{
  const store=storage()
  if(!store)return null
  try{return store.getItem(key)}catch{return null}
}

function write(key:string):void{
  const store=storage()
  if(!store)return
  try{store.setItem(key,'1')}catch{/* 无痕模式等场景下忽略 */}
}

function clear(key:string):void{
  const store=storage()
  if(!store)return
  try{store.removeItem(key)}catch{/* 忽略 */}
}

/** 没有可用的 localStorage 时不自动弹出引导，避免每次刷新都打扰用户。 */
export function guideSeen():boolean{
  if(!storage())return true
  return read(GUIDE_SEEN_KEY)==='1'
}

export function markGuideSeen():void{write(GUIDE_SEEN_KEY)}
export function resetGuide():void{clear(GUIDE_SEEN_KEY)}

export function hintDismissed():boolean{return read(HINT_DISMISSED_KEY)==='1'}
export function dismissHint():void{write(HINT_DISMISSED_KEY)}
