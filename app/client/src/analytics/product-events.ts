// IDs and fixed enums only. Never send dialogue, action text or memory contents.
export function trackProductEvent(name:string,metadata:Record<string,string|number|boolean|null|undefined>={},durationMs?:number){
  window.dispatchEvent(new CustomEvent('product-behavior',{detail:{name,metadata,
    durationMs:durationMs===undefined?undefined:Math.max(0,Math.min(3600000,Math.round(durationMs)))}}))
}

export function activeDuration(start:number,now=performance.now()){
  return Math.max(0,Math.min(60000,Math.round(now-start)))
}
