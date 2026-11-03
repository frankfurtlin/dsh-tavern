import {api,authGeneration,authorizationHeaders,ApiError} from '../api'
import type {Book,Branch,ChapterInfo,Chapter,Bookmark,Progress,Page} from '../api/types'
import {backendBase,cacheSet} from '../platform/bridge'
import {commitPackage,privateScope} from './storage'
import {downloadImages} from '../api/assets'

export async function downloadReading(bookId:string,branchId:string,revisionId:string,origin:string,onProgress:(done:number,total:number)=>void,active:()=>boolean=()=>true){
  const ownerValid=privateScope(),epoch=authGeneration(),valid=()=>ownerValid()&&epoch===authGeneration()&&active()
  const check=()=>{if(!valid())throw new Error('账号、故事线或下载页面已变化')}
  async function get<T>(path:string){check();const value=await api<T>(path);check();return value}
  async function pages<T>(path:string){const items:T[]=[];let cursor:string|null=null;do{const result:Page<T>=await get(path+(cursor?'&cursor='+encodeURIComponent(cursor):''));items.push(...result.items);cursor=result.nextCursor||null}while(cursor);return items}
  const book=await get<Book>('/books/'+bookId),branches=await pages<Branch>(`/books/${bookId}/branches?limit=100`)
  const selected=branches.find(branch=>branch.id===branchId);if(!selected)throw new Error('故事线已不存在，下载未完成')
  const directory=await pages<ChapterInfo>(`/branches/${branchId}/chapters?revisionId=${revisionId}`)
  if(!directory.length)throw new Error('这条故事线还没有可下载的正文')
  const chapters:Chapter[]=[],assets:Record<string,string>={};onProgress(0,directory.length)
  for(const item of directory){
    const chapter=await get<Chapter>(`/branches/${branchId}/chapters/${item.id}?revisionId=${revisionId}`)
    if(chapter.id!==item.id||chapter.revisionId!==revisionId||chapter.blocks.some(block=>block.startAnchor.bookId!==bookId||block.startAnchor.branchId!==branchId||block.startAnchor.revisionId!==revisionId))throw new Error('正文版本发生变化，请重新下载')
    for(const block of chapter.blocks){
      if(!block.html)continue
      const document=new DOMParser().parseFromString(block.html,'text/html')
      for(const image of document.querySelectorAll('img[data-asset]')){
        const id=image.getAttribute('data-asset')||''
        if(!/^[a-zA-Z0-9_-]{1,128}$/.test(id))throw new Error('故事中有无法下载的图片')
        if(assets[id])continue
        check();const response=await fetch(backendBase()+`/api/reader/v1/books/${bookId}/assets/${id}`,{credentials:'include',headers:authorizationHeaders()});check()
        if(!response.ok)throw new ApiError('ASSET_DOWNLOAD_FAILED','图片尚未完整下载，请重试',response.status)
        const blob=await response.blob();check()
        if(!blob.type.startsWith('image/'))throw new Error('图片资源类型异常，下载未完成')
        assets[id]=await new Promise<string>((resolve,reject)=>{const reader=new FileReader();reader.onload=()=>resolve(String(reader.result));reader.onerror=()=>reject(new Error('图片无法保存'));reader.readAsDataURL(blob)});check()
      }
    }
    chapters.push(chapter);onProgress(chapters.length,directory.length)
  }
  const progress=await get<Progress>(`/books/${bookId}/progress?branchId=${branchId}&revisionId=${revisionId}`)
  const images=await downloadImages({kind:'book',bookId,branchId,revisionId,...(progress.maxObservedAnchor?{anchor:progress.maxObservedAnchor}:{})},valid);check()
  const bookmarks=await pages<Bookmark>(`/books/${bookId}/bookmarks?branchId=${branchId}&revisionId=${revisionId}`)
  const origins=['book-'+bookId]
  if(origin&&origin!==origins[0]){
    const anchor=progress.anchor||chapters[0].blocks[0]?.startAnchor
    await api('/stories/'+origin+'/context/validate',{method:'POST',body:{storyId:origin,mode:'read',perspective:'reader',entryOrigin:'reader',bookId,readerBranchId:branchId,anchor}});check();origins.push(origin)
  }
  for(const [id,data] of Object.entries(assets)){check();await cacheSet('asset:'+bookId+':'+id,data)}
  check()
  return commitPackage({book,branch:{...selected,headRevisionId:revisionId},revisionId,directory,chapters,progress,bookmarks,assets,origins,...(images?{images}:{})},valid)
}
