<script setup lang="ts">
import {computed,type Component} from 'vue'
import {useRoute} from 'vue-router'
import {BookOpen,ArrowLeft} from 'lucide-vue-next'
import {pageLocation} from '../workspace/page-location'

type NavItem={path:string;name:string;icon:Component;badge?:string}
const props=defineProps<{navigation:NavItem[];secondary?:NavItem[];groups?:{name:string;items:NavItem[]}[];immersive?:boolean;workEntry?:boolean;mainId:string;scopeLabel:string}>()
const route=useRoute()
const tabs=computed<NavItem[]>(()=>{
 const source=props.groups?.length?props.groups.flatMap(group=>group.items):[...props.navigation,...(props.secondary||[])]
 const seen=new Set<string>()
 return source.filter(item=>{if(seen.has(item.path))return false;seen.add(item.path);return true})
})
const location=computed(()=>pageLocation(route))
const activePath=computed(()=>tabs.value.filter(item=>route.path===item.path||route.path.startsWith(item.path+'/')).sort((a,b)=>b.path.length-a.path.length)[0]?.path||location.value.area.path)
</script>
<template>
 <div class="app-shell" :class="{'app-shell-immersive':immersive,'app-shell-work-entry':workEntry}">
  <a class="shell-skip" :href="'#'+mainId">跳到正文</a>
  <header v-if="!immersive" class="shell-top" :aria-label="scopeLabel">
   <div class="shell-top-left">
    <RouterLink to="/explore" class="shell-brand"><BookOpen :size="24"/><span>酒馆</span></RouterLink>
    <nav class="shell-tabs" aria-label="主导航"><RouterLink v-for="item in tabs" :key="item.path" :to="item.path" active-class="nav-ancestor" :class="{'router-link-active':activePath===item.path}"><component :is="item.icon" :size="18"/><span>{{item.name}}</span><small v-if="item.badge" class="shell-feature-badge">{{item.badge}}</small></RouterLink></nav>
   </div>
   <div class="shell-top-right">
    <div class="shell-header-extra"><slot name="sidebar-footer"/></div>
    <slot name="header-actions"/>
   </div>
  </header>
  <main :id="mainId" tabindex="-1" :class="['shell-content',{'shell-content-immersive':immersive}]"><nav v-if="!immersive&&location.parent&&!location.ownBack" class="shell-page-return" aria-label="返回上一级"><RouterLink :to="location.parent.path"><ArrowLeft :size="17" aria-hidden="true"/>返回{{location.parent.label}}</RouterLink><span>{{location.title}}</span></nav><slot/></main>
  <nav v-if="!immersive" class="shell-mobile-nav" :style="{gridTemplateColumns:'repeat('+navigation.length+',minmax(0,1fr))'}" aria-label="移动主导航"><RouterLink v-for="item in navigation" :key="item.path" :to="item.path" active-class="nav-ancestor" :class="{'router-link-active':activePath===item.path||location.area.path===item.path}"><component :is="item.icon" :size="21"/><span>{{item.name}}</span></RouterLink></nav>
  <slot name="overlay"/>
 </div>
</template>
<style scoped>
.app-shell{min-height:100dvh;color:var(--ink);background:var(--paper)}.shell-skip{position:fixed;top:12px;left:12px;transform:translateY(-180%);z-index:120;padding:12px 20px;background:var(--canvas);border:2px solid var(--accent);border-radius:12px;color:var(--ink)}.shell-skip:focus{transform:none}
.shell-top{position:sticky;top:0;z-index:30;display:flex;align-items:center;justify-content:space-between;gap:20px;min-height:66px;padding:0 clamp(16px,3vw,36px);background:var(--canvas);border-bottom:1px solid var(--edge)}.shell-top-left{display:flex;align-items:center;gap:clamp(14px,2.4vw,32px);min-width:0;flex:1}.shell-brand{display:flex;align-items:center;gap:9px;color:var(--accent);font-family:var(--font-serif);font-size:23px;text-decoration:none;min-height:44px;flex:none}.shell-brand svg{flex:none}.shell-tabs{display:flex;align-items:center;gap:4px;min-width:0;overflow-x:auto;scrollbar-width:none}.shell-tabs::-webkit-scrollbar{display:none}.shell-tabs a{display:flex;align-items:center;gap:7px;min-height:44px;padding:8px 14px;border-radius:10px;color:var(--quiet);text-decoration:none;font-size:14px;white-space:nowrap;transition:background .18s,color .18s}.shell-tabs a:hover{background:var(--wash)}.shell-tabs a.router-link-active,.shell-tabs a.nav-ancestor{color:var(--accent);background:var(--wash);font-weight:600}.shell-top-right{display:flex;align-items:center;gap:8px;flex:none}.shell-header-extra{display:flex;align-items:center;gap:4px}.shell-header-extra:empty{display:none}.shell-header-extra :deep(a),.shell-header-extra :deep(button){min-height:44px;justify-content:start;text-decoration:none!important}.shell-header-extra :deep(.final-lock){padding-inline:12px;background:transparent;border-color:transparent}
.shell-content{width:100%;max-width:1460px;margin-inline:auto;padding:36px clamp(24px,4vw,56px) 72px;min-width:0}.shell-content.shell-content-immersive{max-width:none;padding:0;margin:0}.app-shell-immersive .shell-top,.app-shell-immersive .shell-mobile-nav{display:none}.shell-mobile-nav{display:none}
.shell-content :deep(.final-page-heading){margin-bottom:28px;align-items:center}.shell-content :deep(.final-eyebrow){display:none}.shell-content :deep(h1){font-size:32px;letter-spacing:.01em}.shell-content :deep(.final-heading-copy){gap:6px}.shell-content :deep(.final-empty){border:0;padding:72px 24px;background:transparent}.shell-content :deep(.page-heading h1){font-size:32px}.shell-content :deep(.world-list-heading){margin-bottom:28px}
.app-shell-work-entry .shell-content{max-width:none;height:calc(100dvh - 66px);min-height:0;display:flex;flex-direction:column;overflow:hidden;padding:14px 20px 20px}.app-shell-work-entry .shell-content :deep(.story-entry){width:100%;flex:1;min-height:0;display:flex;flex-direction:column}.app-shell-work-entry .shell-content :deep(.story-entry-top){flex:none;min-height:36px;margin:0 0 8px;font-size:12px}.app-shell-work-entry .shell-content :deep(.story-entry-top .story-back){min-height:36px}
@media(min-width:1920px){.shell-content{padding-inline:64px}}
@media(min-width:768px) and (max-width:1023px){.shell-top{padding-inline:24px}.shell-content{padding:32px 24px 64px}}
@media(max-width:767px){.shell-top{min-height:calc(60px + env(safe-area-inset-top));padding:8px 16px;padding-top:calc(8px + env(safe-area-inset-top))}.shell-top-left{gap:12px}.shell-brand{font-size:20px}.shell-tabs{display:none}.shell-header-extra{display:none}.shell-content{padding:24px 16px calc(104px + env(safe-area-inset-bottom))}.shell-content-immersive{padding:0}.shell-content :deep(h1),.shell-content :deep(.page-heading h1){font-size:28px}.shell-content :deep(.final-page-heading){gap:12px;flex-wrap:wrap;margin-bottom:24px}.shell-content :deep(.final-page-heading>.final-heading-copy),.shell-content :deep(.final-page-heading>h1){flex:1;min-width:100px}.shell-content :deep(.final-page-actions){gap:8px;flex-wrap:wrap}.shell-mobile-nav{display:grid;position:fixed;inset:auto 0 0;z-index:35;gap:2px;padding:8px 8px calc(8px + env(safe-area-inset-bottom));border-top:1px solid var(--edge);background:var(--canvas)}.shell-mobile-nav a{display:flex;flex-direction:column;align-items:center;justify-content:center;gap:4px;min-height:52px;min-width:0;border-radius:12px;color:var(--quiet);font-size:12px;text-decoration:none}.shell-mobile-nav a.router-link-active,.shell-mobile-nav a.nav-ancestor{color:var(--accent);background:var(--wash)}.shell-content :deep(.final-empty){padding:48px 8px}.app-shell-work-entry .shell-content{height:calc(100dvh - 60px - env(safe-area-inset-top));padding:20px 16px calc(100px + env(safe-area-inset-bottom))}}
@media(max-width:360px){.shell-tabs a{padding-inline:10px;font-size:13px}}
</style>
