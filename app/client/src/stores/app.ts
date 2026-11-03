import {defineStore} from 'pinia'
import {ref,computed} from 'vue'
import {api,setAccess,refreshAuth,refreshedSession,authGeneration,beginAuthentication,ApiError} from '../api'
import {parseSessionBootstrap,type DefaultModels} from '../api/session-bootstrap'
import type {User,ReadingSettings} from '../api/types'
import {cacheSet,cacheGet,clearPrivate,setAccount,secureRefresh,clientType,accountId,backendBase} from '../platform/bridge'
export const useApp=defineStore('app',()=>{
  const user=ref<User|null>(null),online=ref(navigator.onLine),notice=ref(''),booted=ref(false)
  const worldEnabled=ref(false)
  const defaultModels=ref<DefaultModels>()
  const exitingAccount=ref(false)
  window.addEventListener('reader-private-cleared',()=>{user.value=null;worldEnabled.value=false;defaultModels.value=undefined})
  async function loadWorldConfig(){const epoch=authGeneration();try{const config=await api<{enabled:boolean;ready:boolean}>('/config',{namespace:'world'});if(epoch===authGeneration())worldEnabled.value=config.enabled&&config.ready}catch{if(epoch===authGeneration())worldEnabled.value=false}}
  const viewportWidth=ref(innerWidth)
  window.addEventListener('resize',()=>{viewportWidth.value=innerWidth})
  const settings=computed(()=>{
    const saved=user.value?.settings||{version:1,fontSize:18,lineHeight:1.8,theme:'light' as const}
    return saved.version===1&&saved.fontSize===18?{...saved,fontSize:viewportWidth.value>=1024?20:18}:saved
  })
  let noticeTimer:ReturnType<typeof setTimeout>|undefined
  let loginGeneration=0
  function message(text:string){clearTimeout(noticeTimer);notice.value=text;noticeTimer=setTimeout(()=>{if(notice.value===text)notice.value=''},4000)}
  async function init(){
    const epoch=authGeneration(),owner=accountId(),base=backendBase()
    const sameScope=()=>epoch===authGeneration()&&owner===accountId()&&base===backendBase()&&!localStorage.getItem('reader-session-revoked')
    async function restoreCachedIdentity(){
      if(!owner||!sameScope())return
      let cached:User|undefined
      try{cached=await cacheGet<User>('identity')}catch{return}
      if(!sameScope()||cached?.id!==owner)return
      user.value=cached;online.value=false
    }
    let configured=false
    if(await refreshAuth())try{const startup=refreshedSession(),current=startup?.user??await api<User>('/auth/me');if(!sameScope()||owner&&current.id!==owner)return;setAccount(current.id);await cacheSet('identity',current);if(epoch!==authGeneration()||accountId()!==current.id||base!==backendBase()||localStorage.getItem('reader-session-revoked'))return;user.value=current;if(startup){worldEnabled.value=startup.world.enabled&&startup.world.ready;defaultModels.value=startup.defaultModels;configured=true}}catch(e){
      if(sameScope()){
        user.value=null
        // A successful refresh can use the service worker transport while the
        // following document request is offline. It does not revoke identity.
        if(e instanceof ApiError&&e.code==='NETWORK_UNAVAILABLE'&&e.status===0)await restoreCachedIdentity()
      }
    }
    else await restoreCachedIdentity()
    booted.value=true
    if(user.value&&!configured)await loadWorldConfig()
  }
  async function login(username:string,password:string,active:()=>boolean=()=>true){
    exitingAccount.value=false
    const generation=++loginGeneration,owner=accountId()
    let epoch=beginAuthentication()
    const check=()=>{if(epoch!==authGeneration()||generation!==loginGeneration||!active())throw new ApiError('SESSION_CHANGED','登录期间账号、页面或服务器已变化',401)}
    const data=await api<{accessToken:string;refreshToken?:string;sessionId:string;user:{id:string}}>('/auth/login',{method:'POST',body:{username,password,clientType,deviceName:clientType==='web'?'浏览器':clientType+' App'}})
    check();if(accountId()!==owner)throw new ApiError('SESSION_CHANGED','登录期间账号已变化',401)
    if(owner&&owner!==data.user.id){const clearing=clearPrivate();epoch=authGeneration();await clearing;check();if(accountId())throw new ApiError('SESSION_CHANGED','登录期间账号已变化',401)}
    setAccess(data.accessToken);setAccount(data.user.id)
    if(data.refreshToken){await secureRefresh(data.refreshToken);check()}
    const startup=parseSessionBootstrap(data),current=startup?.user??await api<User>('/auth/me');check()
    if(current.id!==data.user.id||accountId()!==data.user.id)throw new ApiError('SESSION_CHANGED','登录期间账号已变化',401)
    // Publish identity only after login setup settles. Publishing it can change
    // the root layout and unmount the form that owns this request's scope.
    await cacheSet('identity',current);check()
    if(startup){worldEnabled.value=startup.world.enabled&&startup.world.ready;defaultModels.value=startup.defaultModels}else await loadWorldConfig();check()
    localStorage.removeItem('reader-session-revoked')
    user.value=current
    return epoch
  }
  async function logout(){exitingAccount.value=true;
    const epoch=authGeneration(),owner=accountId()
    try{await api('/auth/logout',{method:'POST'})}catch{}
    if(epoch!==authGeneration()||owner!==accountId())throw new ApiError('SESSION_CHANGED','账号已变化，请重新打开设置',401)
    ++loginGeneration;user.value=null;setAccess('');const clearing=clearPrivate(),clearedEpoch=authGeneration();await clearing
    if(clearedEpoch!==authGeneration()||accountId())throw new ApiError('SESSION_CHANGED','清理期间账号已变化',401)
    return clearedEpoch
  }
  async function saveSettings(value:ReadingSettings){
    const epoch=authGeneration(),owner=user.value?.id
    if(!owner)throw new ApiError('SESSION_CHANGED','账号已变化，请重新打开设置',401)
    const saved=await api<ReadingSettings>('/settings',{method:'PUT',body:{expectedVersion:value.version,fontSize:value.fontSize,lineHeight:value.lineHeight,theme:value.theme,readingMode:value.readingMode,readingWidth:value.readingWidth,chapterLength:value.chapterLength}})
    if(epoch!==authGeneration()||user.value?.id!==owner)throw new ApiError('SESSION_CHANGED','账号已变化，请重新打开设置',401)
    user.value.settings=saved;await cacheSet('identity',user.value)
    if(epoch!==authGeneration()||user.value?.id!==owner)throw new ApiError('SESSION_CHANGED','账号已变化，请重新打开设置',401)
  }
  return{user,online,notice,booted,settings,worldEnabled,defaultModels,exitingAccount,init,login,logout,saveSettings,message,loadWorldConfig}
})
