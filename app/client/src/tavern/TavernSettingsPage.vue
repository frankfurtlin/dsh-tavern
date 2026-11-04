<script setup lang="ts">
import {BookMarked,Brush,Download,SlidersHorizontal,UserRound} from 'lucide-vue-next'
const items=[
  {title:'人设（我是谁）',detail:'选择或编辑你在对话中扮演的身份。',icon:UserRound},
  {title:'预设与采样',detail:'主提示词、越狱与后置提示词、temperature / top_p 等参数。',icon:SlidersHorizontal},
  {title:'正则替换',detail:'保存回复时执行的输出替换规则。',icon:Brush},
  {title:'导入与导出',detail:'角色卡导入、聊天记录导入导出。',icon:Download},
  {title:'世界书',detail:'在角色详情页维护每张卡的世界书条目。',icon:BookMarked},
]
</script>

<template>
 <section class="tavern-settings" aria-label="设置">
  <h1>设置</h1>
  <p class="muted">酒馆 2.0 正在迁移，以下模块会在后续版本开放；所有数据都保存在你自己的账号下。</p>
  <ul class="settings-list">
   <li v-for="item in items" :key="item.title" class="settings-item">
    <component :is="item.icon" :size="20" aria-hidden="true"/>
    <div>
     <p class="settings-title">{{item.title}}<span class="badge">即将开放</span></p>
     <p class="settings-detail">{{item.detail}}</p>
    </div>
   </li>
  </ul>
  <RouterLink to="/" class="settings-back">返回角色卡</RouterLink>
 </section>
</template>

<style scoped>
.tavern-settings{width:100%;max-width:720px;margin:0 auto;padding:24px clamp(14px,3vw,28px) 72px}
h1{font-family:var(--font-serif);font-size:28px;margin:0 0 8px}
.muted{color:var(--quiet);font-size:14px;line-height:1.7;margin:0 0 24px}
.settings-list{list-style:none;margin:0;padding:0;display:flex;flex-direction:column;gap:10px}
.settings-item{display:flex;gap:14px;align-items:flex-start;padding:14px;border:1px solid var(--edge);border-radius:14px;background:var(--canvas);color:var(--quiet)}
.settings-item>svg{flex:none;margin-top:2px;color:var(--accent)}
.settings-title{display:flex;align-items:center;gap:8px;margin:0 0 4px;font-size:15px;color:var(--ink)}
.settings-detail{margin:0;font-size:13px;line-height:1.6}
.settings-back{display:inline-flex;margin-top:28px;min-height:44px;align-items:center;color:var(--accent)}
</style>
