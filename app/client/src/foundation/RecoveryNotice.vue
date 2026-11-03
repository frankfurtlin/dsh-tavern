<script setup lang="ts">
import {ref} from 'vue'
import {ShieldCheck,Download,X} from 'lucide-vue-next'
import {useApp} from '../stores/app'
import {recoveryNotice} from './recovery-notice'
const store=useApp(),revealed=ref(false)
function download(){const saved=recoveryNotice.value;if(!saved||saved.owner!==store.user?.id)return;const url=URL.createObjectURL(new Blob(['故事之间 · 账号恢复码\n用户名：'+store.user.username+'\n恢复码：'+saved.code+'\n\n请保存在安全的位置。恢复码仅能使用一次，任何持有码的人都能重设密码。'],{type:'text/plain;charset=utf-8'}));const link=document.createElement('a');link.href=url;link.download='故事之间-账号恢复码.txt';link.click();setTimeout(()=>URL.revokeObjectURL(url),1000)}
</script>
<template><section v-if="recoveryNotice&&recoveryNotice.owner===store.user?.id" class="onboarding-security" aria-label="保存账号恢复码"><div class="row between"><h2><ShieldCheck :size="20"/>账号已创建，请保存恢复码</h2><button class="icon-button" aria-label="关闭恢复码提示" @click="recoveryNotice=null"><X :size="18"/></button></div><p>忘记密码时，可用恢复码找回账号。关闭页面后将无法再次查看，可在账号设置中重新生成。</p><code v-if="revealed" class="recovery-code">{{recoveryNotice.code}}</code><div class="actions"><button @click="revealed=!revealed">{{revealed?'隐藏恢复码':'查看恢复码'}}</button><button @click="download"><Download :size="17"/>下载并妥善保存</button><button class="text-action" @click="recoveryNotice=null">已保存，关闭提示</button></div></section></template>
