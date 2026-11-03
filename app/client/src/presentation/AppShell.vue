<script setup lang="ts">
import {ref,watch,computed,nextTick,type Component} from 'vue'
import {useRoute} from 'vue-router'
import {BookOpen,Menu,X,ChevronRight,ArrowLeft} from 'lucide-vue-next'
import {pageLocation} from '../workspace/page-location'

type NavItem={path:string;name:string;icon:Component;badge?:string}
const props=defineProps<{navigation:NavItem[];secondary?:NavItem[];groups?:{name:string;items:NavItem[]}[];immersive?:boolean;workEntry?:boolean;mainId:string;scopeLabel:string}>()
const route=useRoute(),menu=ref(false),menuButton=ref<HTMLButtonElement>(),menuClose=ref<HTMLButtonElement>()
const allNavigation=computed(()=>props.groups?.flatMap(g=>g.items)||[...props.navigation,...(props.secondary||[])])
const location=computed(()=>pageLocation(route))
const activePath=computed(()=>allNavigation.value.filter(item=>route.path===item.path||route.path.startsWith(item.path+'/')).sort((a,b)=>b.path.length-a.path.length)[0]?.path||location.value.area.path)
watch(()=>route.fullPath,()=>{if(menu.value){menu.value=false;menuButton.value?.focus()}})
watch(menu,async open=>{if(open){await nextTick();menuClose.value?.focus()}})
function closeMenu(){menu.value=false;menuButton.value?.focus()}
</script>
<template>
 <div class="app-shell" :class="{'app-shell-immersive':immersive,'app-shell-work-entry':workEntry}">
  <a class="shell-skip" :href="'#'+mainId">跳到正文</a>
  <aside v-if="!immersive" class="shell-sidebar">
   <RouterLink :to="navigation[0]?.path||'/'" class="shell-brand"><BookOpen :size="25"/><span>酒馆</span></RouterLink>
   <span class="shell-scope">{{scopeLabel}}</span>
   <div class="shell-navigation"><template v-if="groups"><section v-for="group in groups" :key="group.name"><p class="shell-nav-label">{{group.name}}</p><nav :aria-label="group.name"><RouterLink v-for="item in group.items" :key="item.path" :to="item.path" active-class="nav-ancestor" :class="{'router-link-active':activePath===item.path}"><component :is="item.icon" :size="20"/><span>{{item.name}}</span><small v-if="item.badge" class="shell-feature-badge">{{item.badge}}</small></RouterLink></nav></section></template><template v-else><nav aria-label="主导航"><RouterLink v-for="item in navigation" :key="item.path" :to="item.path" active-class="nav-ancestor" :class="{'router-link-active':activePath===item.path}"><component :is="item.icon" :size="21"/><span>{{item.name}}</span></RouterLink></nav><template v-if="secondary?.length"><p class="shell-nav-label">创作与管理</p><nav aria-label="工具导航"><RouterLink v-for="item in secondary" :key="item.path" :to="item.path" active-class="nav-ancestor" :class="{'router-link-active':activePath===item.path}"><component :is="item.icon" :size="19"/><span>{{item.name}}</span></RouterLink></nav></template></template></div>
   <div class="shell-sidebar-footer"><slot name="sidebar-footer"/></div>
  </aside>
  <div class="shell-workspace">
   <header v-if="!immersive" class="shell-desktop-header"><nav class="shell-breadcrumb" aria-label="当前位置"><RouterLink to="/explore">酒馆</RouterLink><ChevronRight :size="14"/><RouterLink v-if="location.parent&&location.area.path!==route.path" :to="location.area.path">{{location.area.label}}</RouterLink><ChevronRight v-if="location.parent&&location.area.path!==route.path" :size="14"/><strong aria-current="page">{{location.title}}</strong></nav><slot name="header-actions"/></header>
   <header v-if="!immersive" class="shell-mobile-header"><div class="row"><button ref="menuButton" aria-label="打开全部功能" :aria-expanded="menu" aria-controls="workspace-mobile-menu" @click="menu=!menu"><Menu :size="22"/></button><RouterLink :to="navigation[0]?.path||'/'" class="shell-brand"><BookOpen :size="21"/><span>酒馆</span></RouterLink></div><slot name="header-actions"/></header>
   <section v-if="menu&&!immersive" id="workspace-mobile-menu" class="shell-menu" aria-label="全部功能" @keydown.esc="closeMenu"><div class="row between"><strong>{{scopeLabel}}</strong><button ref="menuClose" aria-label="关闭全部功能" @click="closeMenu"><X :size="20"/></button></div><nav><RouterLink v-for="item in allNavigation" :key="item.path" :to="item.path" active-class="nav-ancestor" :class="{'router-link-active':activePath===item.path}"><component :is="item.icon" :size="20"/>{{item.name}}<small v-if="item.badge" class="shell-feature-badge">{{item.badge}}</small></RouterLink></nav><slot name="sidebar-footer"/></section>
   <main :id="mainId" tabindex="-1" :class="['shell-content',{'shell-content-immersive':immersive}]"><nav v-if="!immersive&&location.parent&&!location.ownBack" class="shell-page-return" aria-label="返回上一级"><RouterLink :to="location.parent.path"><ArrowLeft :size="17" aria-hidden="true"/>返回{{location.parent.label}}</RouterLink><span>{{location.title}}</span></nav><slot/></main>
  </div>
  <nav v-if="!immersive" class="shell-mobile-nav" :style="{gridTemplateColumns:'repeat('+navigation.length+',minmax(0,1fr))'}" aria-label="移动主导航"><RouterLink v-for="item in navigation" :key="item.path" :to="item.path" active-class="nav-ancestor" :class="{'router-link-active':activePath===item.path||location.area.path===item.path}"><component :is="item.icon" :size="21"/><span>{{item.name}}</span></RouterLink></nav>
  <slot name="overlay"/>
 </div>
</template>
<style scoped>
.app-shell{--shell-width:224px;min-height:100dvh;color:var(--ink);background:var(--paper)}.shell-skip{position:fixed;top:12px;left:12px;transform:translateY(-180%);z-index:120;padding:12px 20px;background:var(--canvas);border:2px solid var(--accent);border-radius:12px;color:var(--ink)}.shell-skip:focus{transform:none}.shell-sidebar{position:fixed;inset:0 auto 0 0;width:var(--shell-width);display:flex;flex-direction:column;padding:32px 20px 24px;background:var(--canvas);border-right:1px solid var(--edge);z-index:20}.shell-brand{display:flex;align-items:center;gap:11px;color:var(--accent);font-family:var(--font-serif);font-size:25px;text-decoration:none;min-height:44px}.shell-brand svg{flex:none}.shell-scope{font-size:12px;color:var(--quiet);margin:8px 0 38px 36px}.shell-sidebar nav{display:grid;gap:7px}.shell-sidebar nav a{display:flex;align-items:center;gap:13px;min-height:52px;padding:12px 16px;border-radius:12px;color:var(--quiet);text-decoration:none;transition:background .18s,color .18s}.shell-sidebar nav a:hover{background:var(--wash)}.shell-sidebar nav a.router-link-active{color:var(--accent);background:var(--wash);font-weight:600}.shell-sidebar-footer{margin-top:auto;padding-top:32px;display:grid;gap:12px;min-width:0}.shell-workspace{margin-left:var(--shell-width);min-width:0}.shell-content{width:100%;max-width:1408px;margin-inline:auto;padding:44px clamp(24px,4vw,64px) 72px;min-width:0}.shell-content.shell-content-immersive{max-width:none;padding:0;margin:0}.app-shell-immersive .shell-workspace{margin:0}.shell-mobile-header,.shell-mobile-nav{display:none}.shell-content :deep(.final-page-heading){margin-bottom:28px;align-items:center}.shell-content :deep(.final-eyebrow){display:none}.shell-content :deep(h1){font-size:32px;letter-spacing:.01em}.shell-content :deep(.final-heading-copy){gap:6px}.shell-content :deep(.final-empty){border:0;padding:72px 24px;background:transparent}.shell-sidebar-footer :deep(a),.shell-sidebar-footer :deep(button){min-height:44px;justify-content:start;text-decoration:none!important}.shell-sidebar-footer :deep(.final-lock){margin-top:0;padding-inline:12px;background:transparent;border-color:transparent}.shell-sidebar-footer :deep(.shell-account){display:flex;align-items:center;gap:10px;color:var(--quiet);font-size:13px;padding:12px;min-width:0}.shell-sidebar-footer :deep(.shell-account span){overflow:hidden;text-overflow:ellipsis;white-space:nowrap}.shell-mobile-header :deep(button),.shell-mobile-header :deep(a:not(.shell-brand)){min-height:44px;min-width:44px;display:grid;place-items:center;border:0;background:transparent;color:var(--accent);padding:10px}.shell-content :deep(.page-heading h1){font-size:32px}.shell-content :deep(.world-list-heading){margin-bottom:28px}
@media(min-width:1920px){.shell-content{padding-inline:64px}}@media(min-width:768px) and (max-width:1023px){.app-shell{--shell-width:180px}.shell-sidebar{padding:28px 12px}.shell-brand{font-size:21px;gap:8px}.shell-scope{margin-left:30px}.shell-content{padding:36px 24px 64px}}
@media(max-width:767px){.shell-sidebar{display:none}.shell-workspace{margin:0}.shell-mobile-header{display:flex;align-items:center;justify-content:space-between;gap:12px;padding:8px 20px;min-height:calc(60px + env(safe-area-inset-top));padding-top:calc(8px + env(safe-area-inset-top));border-bottom:1px solid var(--edge);background:var(--canvas)}.shell-brand{font-size:21px}.shell-content{padding:26px 20px calc(108px + env(safe-area-inset-bottom))}.shell-content-immersive{padding:0}.shell-content :deep(h1),.shell-content :deep(.page-heading h1){font-size:28px}.shell-content :deep(.final-page-heading){gap:12px;flex-wrap:wrap;margin-bottom:24px}.shell-content :deep(.final-page-heading>.final-heading-copy),.shell-content :deep(.final-page-heading>h1){flex:1;min-width:100px}.shell-content :deep(.final-page-actions){gap:8px;flex-wrap:wrap}.shell-mobile-nav{display:grid;grid-template-columns:repeat(5,minmax(0,1fr));position:fixed;inset:auto 0 0;z-index:35;gap:2px;padding:8px 8px calc(8px + env(safe-area-inset-bottom));border-top:1px solid var(--edge);background:var(--canvas)}.shell-mobile-nav a{display:flex;flex-direction:column;align-items:center;justify-content:center;gap:4px;min-height:52px;min-width:0;border-radius:12px;color:var(--quiet);font-size:12px;text-decoration:none}.shell-mobile-nav a.router-link-active{color:var(--accent);background:var(--wash)}.shell-content :deep(.final-empty){padding:48px 8px}}
</style>
<style scoped>
@media(min-width:768px){
 .app-shell-work-entry{--shell-width:184px}
 .app-shell-work-entry .shell-sidebar{padding:24px 16px}
 .app-shell-work-entry .shell-brand{font-size:23px}
 .app-shell-work-entry .shell-scope{margin:6px 0 28px 35px}
 .app-shell-work-entry .shell-content{max-width:none;height:100dvh;min-height:0;display:flex;flex-direction:column;overflow:hidden;padding:14px 20px 20px}
 .app-shell-work-entry .shell-content :deep(.story-entry){width:100%;flex:1;min-height:0;display:flex;flex-direction:column}
 .app-shell-work-entry .shell-content :deep(.story-entry-top){flex:none;min-height:36px;margin:0 0 8px;font-size:12px}
 .app-shell-work-entry .shell-content :deep(.story-entry-top .story-back){min-height:36px}
}
@media(min-width:2400px){.app-shell-work-entry .shell-content{padding:16px 24px 24px}}
</style>
