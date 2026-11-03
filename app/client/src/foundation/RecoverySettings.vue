<script setup lang="ts">
import {ref,onUnmounted} from 'vue'
import {ShieldCheck,Download} from 'lucide-vue-next'
import {api} from '../api'
import {useApp} from '../stores/app'
import {useRequestScope} from '../platform/request-scope'
const store=useApp(),capture=useRequestScope(),password=ref(''),code=ref(''),busy=ref(false),error=ref('');let attempt=0
async function generate(){if(busy.value)return;const scope=capture(),version=++attempt;busy.value=true;error.value='';try{const result=await api<{recoveryCode:string}>('/auth/recovery-code',{method:'POST',body:{currentPassword:password.value}});if(scope.valid()&&version===attempt){code.value=result.recoveryCode;password.value=''}}catch(e){if(scope.valid()&&version===attempt)error.value=(e as Error).message}finally{if(scope.valid()&&version===attempt)busy.value=false}}
function download(){if(!code.value)return;const url=URL.createObjectURL(new Blob(['故事之间 · 账号恢复码\n用户名：'+store.user?.username+'\n恢复码：'+code.value+'\n\n仅可使用一次，重新生成后旧码失效。请妥善保管。'],{type:'text/plain;charset=utf-8'}));const link=document.createElement('a');link.href=url;link.download='故事之间-账号恢复码.txt';link.click();setTimeout(()=>URL.revokeObjectURL(url),1000)}
onUnmounted(()=>{attempt++;code.value='';password.value=''})
</script>
<template><section class="setting-row"><h2 class="row"><ShieldCheck :size="20"/>账号恢复码</h2><p class="muted">忘记密码时可用恢复码找回账号。每个码只能使用一次，重新生成会让旧码立即失效。</p><p v-if="error" class="error" role="alert">{{error}}</p><template v-if="code"><code class="recovery-code">{{code}}</code><p class="muted">此恢复码只显示这一次，请保存后关闭。持有码的人可以重设账号密码。</p><div class="actions"><button @click="download"><Download :size="17"/>下载恢复码</button><button @click="code=''">已保存，隐藏恢复码</button></div></template><form v-else @submit.prevent="generate"><label>输入当前密码以生成恢复码<input v-model="password" type="password" autocomplete="current-password" required maxlength="128" :disabled="busy"></label><button :disabled="busy||!store.online">{{busy?'正在生成…':'生成新的恢复码'}}</button></form></section></template>
