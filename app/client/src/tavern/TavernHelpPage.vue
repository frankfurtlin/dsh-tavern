<script setup lang="ts">
import {useRouter} from 'vue-router'
import {resetGuide} from './guide'

const router=useRouter()

const sections=[
  {title:'开始聊天',lines:[
    '角色卡库点任意一张卡，立即进入单人聊天；新建的聊天会自动带出开场白。',
    '输入框上方会给出示例短句，点一下只是填入，不会自动发送。',
    '群聊从角色卡库里多选 2–5 张卡发起，回复会按成员顺序轮转。',
  ]},
  {title:'DSH 工具条',lines:[
    '聊天页右上角工具条可以把当前聊天上下文交给 DSH：续写 / 角色扮演。',
    'DSH 只会收到最近 12 条消息（每条最多 1000 字）与角色设定，不会拿到你的账号信息。',
  ]},
  {title:'导入角色卡',lines:[
    '支持 SillyTavern 的 PNG（内嵌 chara 数据）与 JSON 角色卡，单张或批量导入。',
    '导入的角色卡包含角色设定、示例对话与世界书；同名卡不会覆盖，可在卡片设置里改名。',
    '「导入示例卡包」可以一次性添加 5 张内置预设卡。',
  ]},
  {title:'预设卡',lines:[
    '预设卡带有「预设」徽标，由 Foreverse Team 提供（CC BY 4.0），可直接聊天。',
    '预设卡删除后不会自动恢复；需要时重新导入或使用示例卡包。',
  ]},
  {title:'导出与备份',lines:[
    '角色详情可导出角色卡 PNG；聊天页菜单可导出聊天记录（JSONL / JSON）。',
    '角色详情支持导入酒馆聊天 JSONL，换设备或重装前建议先导出。',
    '所有数据保存在你自己的账号下，服务端不保存模型密钥。',
  ]},
  {title:'正则替换',lines:[
    '设置 → 输出替换规则：回复保存前按正则改写文字，例如把「。」改成「！」。',
    '替换在服务端执行；流式气泡显示的是替换前的文本。',
  ]},
  {title:'世界书注入',lines:[
    '角色详情可维护世界书条目：关键词、内容、位置、深度、常驻与触发概率。',
    '聊天时命中的条目会注入到提示词里；「注入预览」可以先用示例消息检查命中。',
  ]},
]
</script>

<template>
 <section class="tavern-help" aria-label="使用帮助">
  <p class="help-intro">花两分钟了解酒馆怎么用；也可以随时回来重新查看新手引导。</p>

  <section v-for="section in sections" :key="section.title" class="help-section" :aria-label="section.title">
   <h2>{{section.title}}</h2>
   <ul>
    <li v-for="line in section.lines" :key="line">{{line}}</li>
   </ul>
  </section>

  <div class="help-actions">
   <button type="button" class="primary" aria-label="重新查看新手引导" @click="resetGuide();router.push({path:'/',query:{guide:'1'}})">重新查看引导</button>
   <button type="button" aria-label="返回设置" @click="router.push('/settings')">返回设置</button>
  </div>
 </section>
</template>

<style scoped>
.tavern-help{width:100%;max-width:760px;margin:0 auto;padding:24px clamp(16px,3vw,28px) 72px;display:flex;flex-direction:column;gap:20px}
.tavern-help :focus-visible{outline:2px solid var(--accent-ink);outline-offset:2px}
.help-intro{margin:0;color:var(--quiet);font-size:14px;line-height:1.7}
.help-section{border-top:1px solid var(--edge);padding-top:16px;display:flex;flex-direction:column;gap:8px}
.help-section h2{font-family:var(--font-serif);font-size:17px;font-weight:600;margin:0;color:var(--ink)}
.help-section ul{margin:0;padding-left:18px;display:flex;flex-direction:column;gap:6px}
.help-section li{color:var(--quiet);font-size:14px;line-height:1.7}
.help-actions{display:flex;gap:10px;flex-wrap:wrap;margin-top:8px}
.help-actions button{min-height:44px;padding:0 18px;border-radius:var(--r-control);border:1px solid var(--edge);background:var(--canvas);color:var(--ink);cursor:pointer;font-size:14px}
.help-actions .primary{border-color:var(--accent);background:var(--accent);color:var(--on-accent)}
@media(max-width:767px){
 .tavern-help{padding-bottom:96px}
 .help-actions{position:sticky;bottom:0;background:var(--canvas);border-top:1px solid var(--edge);padding:10px 0 calc(6px + env(safe-area-inset-bottom));margin:0 -16px -96px;padding-left:16px;padding-right:16px}
 .help-actions button{flex:1}
}
</style>
