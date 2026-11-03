export function createLocaleController({ loaders, fallback='zh-CN', initial=fallback, storage, key='foundation.language' }) {
  const cache=new Map(),pending=new Map();let current=fallback,revision=0
  async function load(code){if(cache.has(code))return cache.get(code);if(!pending.has(code))pending.set(code,Promise.resolve().then(loaders[code]).then(mod=>{const messages=mod.default??mod;cache.set(code,messages);return messages}).finally(()=>pending.delete(code)));return pending.get(code)}
  async function switchLocale(code){if(!loaders[code])code=fallback;const requested=++revision;let messages;try{messages=await load(code)}catch{code=fallback;messages=await load(code)}if(requested===revision){current=code;try{storage?.setItem(key,code)}catch{}}return {locale:current,messages:cache.get(current)??messages,applied:requested===revision}}
  const ready=switchLocale(initial)
  return {ready,switchLocale,current:()=>current}
}
