import {shallowRef} from 'vue'
export const recoveryNotice=shallowRef<{owner:string;code:string}|null>(null)
// A recovery secret stays only in memory and disappears on account changes.
window.addEventListener('reader-private-cleared',()=>{recoveryNotice.value=null})
