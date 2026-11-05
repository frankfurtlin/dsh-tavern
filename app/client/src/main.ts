import {createApp} from 'vue'
import {createPinia} from 'pinia'
import {createRouter,createWebHistory} from 'vue-router'
import App from './App.vue'
import {useApp} from './stores/app'
import './styles/main.css'
import './foundation/product.css'
import {installProductFoundation} from './foundation/runtime'
import {installDshBridge} from './dsh-bridge'
import {APP_BASE} from './platform/base'

const app=createApp(App)
app.use(createPinia())

// Tavern 2.0: a single product surface (characters → chat). Every story-mode
// route that used to exist falls through to the character grid so old links
// never land on a deleted screen.
export const router=createRouter({
  history:createWebHistory(APP_BASE),
  scrollBehavior(_to,_from,saved){return saved||{top:0,left:0}},
  routes:[
    {path:'/',component:()=>import('./tavern/TavernCharactersPage.vue'),meta:{title:'角色卡'}},
    {path:'/characters/regex',redirect:'/'},
    {path:'/characters/groups',redirect:'/'},
    {path:'/characters/groups/:id',redirect:'/'},
    {path:'/characters/:id',component:()=>import('./tavern/TavernCharacterPage.vue'),meta:{title:'角色详情'}},
    {path:'/chats',component:()=>import('./tavern/TavernChatsPage.vue'),meta:{title:'聊天历史'}},
    {path:'/chats/:id',component:()=>import('./tavern/TavernChatPage.vue'),meta:{title:'聊天',immersive:true}},
    {path:'/settings/regex',component:()=>import('./tavern/TavernRegexPage.vue'),meta:{title:'输出替换规则'}},
    {path:'/settings',component:()=>import('./tavern/TavernSettingsPage.vue'),meta:{title:'设置'}},
    {path:'/login',component:()=>import('./pages/AuthPage.vue'),meta:{public:true,title:'登录'}},
    {path:'/register',component:()=>import('./pages/AuthPage.vue'),meta:{public:true,title:'注册'}},
    {path:'/recover',component:()=>import('./pages/AuthPage.vue'),meta:{public:true,title:'找回账号'}},
    {path:'/:pathMatch(.*)*',redirect:'/'},
  ],
})
installProductFoundation(router)
router.beforeEach(async to=>{
  const store=useApp()
  if(!store.booted)await store.init()
  if(to.meta.public)return
  if(!store.user&&!['/login','/register','/recover'].includes(to.path))return {path:'/login',query:{next:to.fullPath}}
  if(store.user&&['/login','/register','/recover'].includes(to.path))return {path:'/',query:to.query.next?{next:to.query.next}:{}}
})
installDshBridge({router})
app.use(router)
app.mount('#app')
