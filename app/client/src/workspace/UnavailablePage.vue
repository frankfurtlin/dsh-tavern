<script setup lang="ts">
import {computed} from 'vue'
import {useRoute} from 'vue-router'
import {Clock3,ArrowUpRight} from 'lucide-vue-next'
import PageHeader from './PageHeader.vue'
import {capabilities,statusLabels} from './capabilities'
const route=useRoute()
const pages:Record<string,{title:string;description:string;ids:string[];alternative:string;label:string}>={
 '/multiplayer':{title:'所有多人世界',description:'多人协作暂未开放。你可以先体验私人互动世界，不同世界线分别保存。',ids:['multiplayer'],alternative:'/worlds',label:'体验私人世界'},
 '/community':{title:'故事论坛',description:'论坛与玩家攻略暂未开放。内测期间可以先查看玩法和创作帮助。',ids:['forum','notifications'],alternative:'/help',label:'查看使用帮助'},
 '/rankings':{title:'作品与作者排行榜',description:'排行榜暂未开放。精选案例不以虚构热度或游玩次数排序。',ids:['rankings'],alternative:'/explore',label:'探索精选作品'},
 '/creators':{title:'创作者中心',description:'作者主页随公开作品开放，关注与收益中心尚未开放。',ids:['follow','public-publishing','payout'],alternative:'/my-works',label:'打开我的作品'},
 '/tasks':{title:'任务与成长',description:'平台成长任务、等级与称号暂未开放。故事内的任务与善后可以正常体验。',ids:['quests','daily'],alternative:'/product/wallet',label:'查看每日权益'},
 '/earnings':{title:'创作者收益',description:'创作者分润与提现暂未开放。当前私人创作不会产生可提现收益。',ids:['payout','pricing'],alternative:'/my-works',label:'继续私人创作'},
 '/affiliate':{title:'邀请与联盟计划',description:'内测邀请奖励可用，商业推广佣金与收益结算暂未开放。',ids:['affiliate'],alternative:'/product/wallet',label:'查看邀请与兑换'},
}
const page=computed(()=>pages[route.path]||pages['/community'])
const items=computed(()=>capabilities.filter(c=>page.value.ids.includes(c.id)))
</script>
<template><div class="workspace-page"><PageHeader :title="page.title" eyebrow="开放状态" :description="page.description"/><section class="workspace-empty availability-empty"><Clock3 :size="44"/><span class="capability-badge" data-status="planned">暂未开放</span><h2>当前可用的范围</h2><p>{{page.description}}</p><RouterLink :to="page.alternative" class="world-primary-link">{{page.label}}<ArrowUpRight :size="18"/></RouterLink></section><div class="workspace-feature-grid"><article v-for="c in items" :key="c.id" class="workspace-card"><span class="capability-badge" :data-status="c.status">{{statusLabels[c.status]}}</span><h2>{{c.name}}</h2><p>{{c.detail}}</p></article></div><RouterLink to="/features" class="inline-link workspace-footnote">查看全部功能与开放状态<ArrowUpRight :size="16"/></RouterLink></div></template>
