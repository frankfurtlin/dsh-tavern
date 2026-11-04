import {authorizationHeaders} from '../api'
import {backendBase} from './bridge'

export function safeFileName(name:string,fallback='download'){
  const cleaned=(name||'').replace(/[\\/:*?"<>|\u0000-\u001f]/g,'_').trim()
  return cleaned||fallback
}

// 授权的二进制下载：<a download> 无法携带 Bearer，所以先用 fetch 取 blob。
export async function authorizedBlob(path:string):Promise<Blob>{
  const response=await fetch(backendBase()+'/api/reader/v1'+path,{credentials:'include',headers:authorizationHeaders()})
  if(!response.ok){
    if(response.status===404)throw new Error('服务端暂不支持这个导出，请稍后重试。')
    throw new Error('导出失败（'+response.status+'），请稍后重试。')
  }
  return response.blob()
}

export function saveBlob(blob:Blob,filename:string){
  if(typeof document==='undefined'||typeof URL==='undefined'||typeof URL.createObjectURL!=='function'){
    throw new Error('当前环境不支持下载文件。')
  }
  const url=URL.createObjectURL(blob)
  const link=document.createElement('a')
  link.href=url
  link.download=filename
  link.rel='noopener'
  document.body.appendChild(link)
  link.click()
  link.remove()
  setTimeout(()=>{try{URL.revokeObjectURL(url)}catch{/* 已释放 */}},1000)
}
