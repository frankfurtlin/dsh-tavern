import {createApp} from 'vue'
import {createPinia} from 'pinia'
import {createRouter,createWebHistory} from 'vue-router'
import App from './App.vue'
import {useApp} from './stores/app'
import {native} from './platform/bridge'
import './styles/main.css'
import './world/world.css'
import './foundation/product.css'
import {installProductFoundation} from './foundation/runtime'
import {loginDestination} from './platform/auth-navigation'
import {legacyDestination,ACCOUNT_HOME} from './platform/workspace-routes'
import {prepareWorker} from './platform/worker-startup'
import {installDshBridge} from './dsh-bridge'
import {APP_BASE} from './platform/base'
const app=createApp(App);app.use(createPinia())
export const router=createRouter({history:createWebHistory(APP_BASE),scrollBehavior(to,_from,saved){if(to.meta.immersive)return false;return saved||{top:0,left:0}},routes:[
  {path:'/',redirect:'/explore'},
  {path:'/help/feedback',redirect:'/explore'},
  {path:'/tasks/activity',redirect:'/explore'},
  {path:'/data-use',component:()=>import('./experience/DataUsePage.vue'),meta:{public:true,title:'数据与使用说明'}},
  {path:'/worlds/:worldId/author',component:()=>import('./authoring/AuthorPage.vue'),props:route=>({key:String(route.params.worldId)}),meta:{title:'作者工作台'}},
  {path:'/published/:publicationId',component:()=>import('./authoring/PublicDetailPage.vue'),props:route=>({key:String(route.params.publicationId)}),meta:{title:'公开作品'}},
  {path:'/authors/:authorId',component:()=>import('./authoring/AuthorPublicPage.vue'),props:route=>({key:String(route.params.authorId)}),meta:{title:'作者主页'}},
  {path:'/explore',component:()=>import('./workspace/DiscoveryPage.vue'),meta:{catalog:true,title:'作品探索'}},
  {path:'/features',component:()=>import('./workspace/FeaturePage.vue'),meta:{catalog:true,title:'全部功能'}},
  {path:'/works',component:()=>import('./workspace/CollectionPage.vue'),meta:{title:'所有作品'}},
  {path:'/my-works',redirect:'/works'},
  {path:'/recent',component:()=>import('./workspace/CollectionPage.vue'),meta:{title:'最近游玩'}},
  {path:'/bookmarks',redirect:'/works?favorites=1'},
  {path:'/my-prompts',component:()=>import('./workspace/PromptsPage.vue'),meta:{title:'我的提示词'}},
  {path:'/characters',component:()=>import('./characters/CharactersPage.vue'),meta:{title:'角色卡库'}},
  {path:'/characters/regex',component:()=>import('./characters/RegexPage.vue'),meta:{title:'输出替换规则'}},
  {path:'/characters/groups',component:()=>import('./characters/GroupListPage.vue'),meta:{title:'群聊'}},
  {path:'/characters/groups/:id',component:()=>import('./characters/GroupChatPage.vue'),meta:{title:'群聊'}},
  {path:'/characters/:id',component:()=>import('./characters/CardDetailPage.vue'),meta:{title:'角色卡详情'}},
  {path:'/multiplayer',component:()=>import('./workspace/UnavailablePage.vue'),meta:{catalog:true,title:'多人世界'}},
  {path:'/community',component:()=>import('./workspace/UnavailablePage.vue'),meta:{catalog:true,title:'故事论坛'}},
  {path:'/rankings',component:()=>import('./workspace/UnavailablePage.vue'),meta:{catalog:true,title:'排行榜'}},
  {path:'/creators',component:()=>import('./workspace/UnavailablePage.vue'),meta:{catalog:true,title:'创作者中心'}},
  {path:'/tasks',component:()=>import('./workspace/UnavailablePage.vue'),meta:{title:'任务与成长'}},
  {path:'/earnings',component:()=>import('./workspace/UnavailablePage.vue'),meta:{title:'创作者收益'}},
  {path:'/affiliate',component:()=>import('./workspace/UnavailablePage.vue'),meta:{title:'邀请与联盟'}},
  {path:'/welcome',redirect:'/login'},
  {path:'/help',redirect:'/explore'},
  {path:'/cases/blue-carbuncle',component:()=>import('./foundation/PublicPage.vue'),meta:{public:true,title:'蓝宝石案'}},
  {path:'/product/account',component:()=>import('./foundation/AccountPage.vue')},
  {path:'/product/wallet',component:()=>import('./foundation/WalletPage.vue')},
  {path:'/product/knowledge',component:()=>import('./foundation/KnowledgePage.vue')},
  {path:'/product/admin',component:()=>import('./foundation/AdminPage.vue')},
  {path:'/subscription',redirect:'/product/wallet'},
  {path:'/login',component:()=>import('./pages/AuthPage.vue')},
  {path:'/register',component:()=>import('./pages/AuthPage.vue')},
  {path:'/recover',component:()=>import('./pages/AuthPage.vue'),meta:{public:true,title:'找回账号'}},
  {path:'/settings/import-history',component:()=>import('./workspace/HistoryImportPage.vue'),meta:{title:'导入旧版内容'}},
  {path:'/local',redirect:to=>legacyDestination(to.path)},
  {path:'/local/:pathMatch(.*)*',redirect:to=>legacyDestination(to.path)},
  {path:'/library',component:()=>import('./pages/LibraryPage.vue')},
  {path:'/stories/:storyId',component:()=>import('./pages/StoryPage.vue')},
  {path:'/worlds',component:()=>import('./world/WorldsPage.vue')},
  {path:'/worlds/new',component:()=>import('./world/WorldCreatePage.vue')},
  {path:'/worlds/scripts',component:()=>import('./world/ScriptBreakdownPage.vue')},
  {path:'/worlds/:worldId/play',component:()=>import('./world/WorldPlayPage.vue'),meta:{immersive:true}},
  {path:'/worlds/:worldId/explore',component:()=>import('./world/WorldPlayPage.vue'),meta:{immersive:true}},
  {path:'/worlds/:worldId/read',component:()=>import('./world/WorldReadPage.vue'),meta:{immersive:true}},
  {path:'/worlds/:worldId/lines',component:()=>import('./world/WorldLinesPage.vue')},
  {path:'/books/:bookId/read',component:()=>import('./pages/ReaderPage.vue')},
  {path:'/books/:bookId/branches',component:()=>import('./pages/BranchesPage.vue')},
  {path:'/downloads',component:()=>import('./pages/DownloadsPage.vue')},
  {path:'/notebook',redirect:'/explore'},
  {path:'/insights',component:()=>import('./pages/InsightsPage.vue')},
  {path:'/conversations/world/:sessionId',component:()=>import('./play-controls/WorldChatPage.vue'),props:route=>({key:String(route.params.sessionId)}),meta:{title:'角色独立对话'}},
  {path:'/conversations',redirect:'/explore'},
  {path:'/settings',redirect:'/explore'},
  {path:'/settings/models',redirect:'/explore'},
  {path:'/:pathMatch(.*)*',redirect:ACCOUNT_HOME}
]})
installProductFoundation(router)
router.beforeEach(async to=>{const store=useApp();if(!store.booted)await store.init();if(to.meta.public)return;if(!store.user&&!['/login','/register'].includes(to.path))return {path:'/login',query:{next:to.fullPath}};if(store.user&&['/login','/register'].includes(to.path))return loginDestination(to.query.next,ACCOUNT_HOME)})
async function waitForWorker(worker:ServiceWorker){
  if(worker.state==='activated')return
  await new Promise<void>((resolve,reject)=>{
    const timer=setTimeout(()=>finish(new Error('Service worker activation timed out')),60000)
    function finish(error?:Error){clearTimeout(timer);worker.removeEventListener('statechange',check);error?reject(error):resolve()}
    function check(){if(worker.state==='activated')finish();else if(worker.state==='redundant')finish(new Error('Service worker installation failed'))}
    worker.addEventListener('statechange',check);check()
  })
}
function warmOfflineShell(){
  const status=document.createElement('p');status.setAttribute('role','status')
  Object.assign(status.style,{position:'fixed',bottom:'calc(72px + env(safe-area-inset-bottom))',right:'16px',maxWidth:'calc(100vw - 32px)',padding:'8px 12px',background:'#fafaf8',border:'1px solid #dedbd2',borderRadius:'8px',fontSize:'13px',zIndex:'100',pointerEvents:'none'})
  let generation=0,channel:MessageChannel|undefined,timer:ReturnType<typeof setTimeout>|undefined
  function request(){
    const worker=navigator.serviceWorker.controller;if(!worker)return
    const epoch=++generation;channel?.port1.close();channel?.port2.close();clearTimeout(timer)
    status.textContent='正在准备离线阅读，完成后可断网使用。';if(!status.isConnected)document.body.appendChild(status)
    channel=new MessageChannel()
    function finish(complete:boolean){if(epoch!==generation)return;clearTimeout(timer);channel?.port1.close();channel?.port2.close();if(complete)status.remove();else status.textContent='离线阅读尚未准备完成，联网时可继续使用；重新加载可再次准备。'}
    timer=setTimeout(()=>finish(false),180000)
    channel.port1.onmessage=event=>{if(event.data?.kind==='reader-shell-warm-complete')finish(event.data.complete===true)}
    worker.postMessage({kind:'reader-shell-warm'},[channel.port2])
  }
  // Give the current route and its first data requests priority over the full
  // offline library. Keep the install/activation/auth coordinator unchanged.
  let scheduled:ReturnType<typeof setTimeout>|undefined
  const schedule=()=>{clearTimeout(scheduled);scheduled=setTimeout(request,2000)}
  navigator.serviceWorker.addEventListener('controllerchange',schedule);window.addEventListener('online',schedule)
  schedule()
}
async function start(){
  let checkForWorkerUpdate:(()=>Promise<void>)|undefined
  // Subpath deployment: the offline service worker assumes root scope, so it is
  // disabled here and the app talks to the API directly.
  if(false&&!import.meta.env.DEV&&!native){
    const element=document.getElementById('app')
    if(element&&!element.querySelector('[role="status"]')){const status=document.createElement('p');status.setAttribute('role','status');status.textContent='正在打开你的故事…';element.replaceChildren(status)}
    if(!('serviceWorker' in navigator))throw new Error('Service worker unavailable')
    if(['/login','/register','/recover'].includes(location.pathname.slice(APP_BASE.length))||['/','/explore'].includes(location.pathname)&&!localStorage.getItem('reader-account'))void import('./pages/AuthPage.vue').catch(()=>{})
    const prepared=await prepareWorker(navigator.serviceWorker,{release:import.meta.env.VITE_RELEASE,origin:location.origin,online:navigator.onLine})
    const registration=prepared.registration;checkForWorkerUpdate=prepared.checkForUpdate
    const worker=registration.active||registration.installing||registration.waiting
    if(!worker)throw new Error('Service worker unavailable')
    await waitForWorker(worker)
    const activeWorker=registration.active
    if(navigator.serviceWorker.controller!==activeWorker)await new Promise<void>((resolve,reject)=>{
      const timer=setTimeout(()=>finish(new Error('Service worker control timed out')),10000)
      function finish(error?:Error){clearTimeout(timer);navigator.serviceWorker.removeEventListener('controllerchange',check);error?reject(error):resolve()}
      function check(){if(navigator.serviceWorker.controller===activeWorker)finish()}
      navigator.serviceWorker.addEventListener('controllerchange',check);check()
    })
    await new Promise<void>((resolve,reject)=>{
      const channel=new MessageChannel(),timer=setTimeout(()=>finish(new Error('Authentication coordinator unavailable')),3000)
      function finish(error?:Error){clearTimeout(timer);channel.port1.close();channel.port2.close();error?reject(error):resolve()}
      channel.port1.onmessage=event=>{if(event.data?.kind==='reader-auth-ready'&&event.data.version===1)finish()}
      navigator.serviceWorker.controller!.postMessage({kind:'reader-auth-ready'},[channel.port2])
    })
  }
  app.use(router)
  // Inside the DSH panel iframe this exposes story context to the host; as a
  // normal top-level page it returns immediately and does nothing.
  installDshBridge({router})
  app.mount('#app')
  if(!import.meta.env.DEV&&!native&&navigator.serviceWorker?.controller)void router.isReady().then(()=>{warmOfflineShell();if(checkForWorkerUpdate)setTimeout(()=>{if(navigator.onLine)void checkForWorkerUpdate!().catch(()=>{})},15000)})
}
start().catch(error=>{
  console.error('Reader bootstrap failed:',error instanceof Error?error.message:'Unknown error')
  const element=document.getElementById('app')
  if(element){const message=document.createElement('p'),retry=document.createElement('button');message.setAttribute('role','alert');message.textContent='无法准备安全会话，请检查网络后重试。';retry.textContent='重新加载';retry.onclick=()=>location.reload();element.replaceChildren(message,retry)}
})
