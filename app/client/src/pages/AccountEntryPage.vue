<script setup lang="ts">
import {ref,computed,onMounted,onUnmounted,watch} from 'vue'
import {useRoute,useRouter} from 'vue-router'
import {BookOpen,Eye,EyeOff,ArrowLeft,ShieldCheck,Check} from 'lucide-vue-next'
import {useApp} from '../stores/app'
import {api,authGeneration} from '../api'
import {native,backendBase,changeBackend,loadDeletionReceipt} from '../platform/bridge'
import {useRequestScope} from '../platform/request-scope'
import {loginDestination} from '../platform/auth-navigation'
import {recoveryNotice} from '../foundation/recovery-notice'
const route=useRoute(),router=useRouter(),store=useApp(),capture=useRequestScope()
const username=ref(typeof route.query.username==='string'?route.query.username:''),password=ref(''),error=ref(''),busy=ref(false),server=ref(backendBase()),visible=ref(false)
const inviteCode=ref(''),inviteRequired=ref(false),registrationEnabled=ref(true),configLoaded=ref(false),code=ref(''),recovered=ref(false)
const createdUsername=ref(''),pendingRecovery=ref(''),configFailed=ref(false)
const defaults=ref<{text:{enabled:boolean;modelId:string|null};image:{enabled:boolean;modelId:string|null}}>()
const registering=computed(()=>route.path==='/register'),recovering=computed(()=>route.path==='/recover')
const cleanupStatus=ref('');let requestGeneration=0,cleanupGeneration=0
watch(()=>route.path,()=>{requestGeneration++;password.value='';code.value='';pendingRecovery.value='';createdUsername.value='';visible.value=false;error.value='';busy.value=false;recovered.value=false})
async function loadConfig(){const scope=capture();configFailed.value=false;try{const result=await api<{inviteRequired:boolean;registrationEnabled?:boolean;defaultModels?:typeof defaults.value}>('/product/public/config');if(scope.valid()){inviteRequired.value=result.inviteRequired;registrationEnabled.value=result.registrationEnabled!==false;defaults.value=result.defaultModels;configLoaded.value=true}}catch{if(scope.valid()){configLoaded.value=false;configFailed.value=true}}}
async function checkCleanup(){const scope=capture(),generation=++cleanupGeneration,valid=()=>scope.valid()&&generation===cleanupGeneration;try{const saved=await loadDeletionReceipt();if(!valid()||!saved||saved.base!==backendBase())return;const data=await api<{status:string;deadline:string}>('/auth/deletion-receipts/'+saved.id);if(valid())cleanupStatus.value=data.status==='completed'?'此回执对应的私人数据已清理完成':data.status==='retrying'?'此回执的清理暂时受阻，后台正在重试':'此回执对应的私人数据正在后台清理'}catch{if(valid())cleanupStatus.value='暂时无法查询清理状态，可稍后重新查询'}}
function backendChanged(){server.value=backendBase();cleanupStatus.value='';void checkCleanup();void loadConfig()}
onMounted(()=>{void loadConfig();void checkCleanup();window.addEventListener('reader-backend-changed',backendChanged)})
onUnmounted(()=>{requestGeneration++;password.value='';code.value='';pendingRecovery.value='';window.removeEventListener('reader-backend-changed',backendChanged)})
async function submit(){
 if(busy.value)return
 const scope=capture(),generation=++requestGeneration,name=username.value.trim(),secret=password.value,isRegistering=registering.value,valid=()=>scope.valid()&&generation===requestGeneration
 error.value='';busy.value=true
 try{
  if(recovering.value){
   await api('/auth/recover',{method:'POST',body:{username:name,recoveryCode:code.value.trim(),newPassword:secret}})
   if(!valid())return
   // Recovery revokes server sessions. Drop the current identity before offering
   // login so the route guard cannot send the expired account back into a story.
   const epoch=await store.logout()
   if(scope.valid(false)&&generation===requestGeneration&&epoch===authGeneration()){password.value='';code.value='';recovered.value=true}
   return
  }
  if(isRegistering&&createdUsername.value!==name){const result=await api<{recoveryCode?:string}>('/auth/register',{method:'POST',body:{username:name,password:secret,...(inviteCode.value.trim()?{inviteCode:inviteCode.value.trim()}:{})}});if(!valid())return;createdUsername.value=name;pendingRecovery.value=result.recoveryCode||''}
  if(!valid())return
  const epoch=await store.login(name,secret,()=>scope.valid(false)&&generation===requestGeneration)
  if(!scope.valid(false)||generation!==requestGeneration||epoch!==authGeneration())return
  if(isRegistering&&createdUsername.value===name&&pendingRecovery.value&&store.user)recoveryNotice.value={owner:store.user.id,code:pendingRecovery.value}
  password.value='';await router.push(loginDestination(route.query.next))
 }catch(e){if(scope.valid(false)&&generation===requestGeneration)error.value=(e as Error).message}finally{if(scope.valid(false)&&generation===requestGeneration)busy.value=false}
}
async function configure(){const scope=capture(),generation=++requestGeneration;try{await changeBackend(server.value,()=>scope.valid()&&generation===requestGeneration);if(scope.valid(false)&&generation===requestGeneration){store.online=true;store.message('后端已连接')}}catch(e){if(scope.valid(false)&&generation===requestGeneration)error.value=(e as Error).message}}
</script>
<template>
 <div class="entry-page" :class="{'entry-native':native}">
  <header class="entry-header"><RouterLink to="/login" class="entry-brand"><BookOpen :size="26"/>酒馆</RouterLink><RouterLink to="/help">使用帮助</RouterLink></header>
  <div class="entry-layout">
   <section class="entry-form-card" aria-label="账号登录与注册">
    <p v-if="cleanupStatus" class="hint" role="status">{{cleanupStatus}}<button @click="checkCleanup">查询清理状态</button></p>
    <nav v-if="!recovering" class="entry-tabs" aria-label="账号入口"><RouterLink :to="{path:'/login',query:route.query.next?{next:route.query.next}:{}}">登录</RouterLink><RouterLink v-if="registrationEnabled" :to="{path:'/register',query:route.query.next?{next:route.query.next}:{}}">注册</RouterLink></nav>
    <RouterLink v-else :to="{path:'/login',query:route.query.next?{next:route.query.next}:{}}" class="entry-back"><ArrowLeft :size="16"/>返回登录</RouterLink>
    <h1>{{recovering?'找回你的账号':registering?'开启你的第一段故事':'欢迎回来'}}</h1>
    <p class="entry-description">{{recovering?'使用注册时保存的恢复码重设密码。':registering?'创建账号后即可保存世界、阅读进度和角色对话。':'登录后继续你的故事与探索。'}}</p>
    <div v-if="recovered" class="entry-success" role="status"><ShieldCheck :size="25"/><h3>密码已重设</h3><p>所有旧登录已退出。恢复码已经失效，登录后请在账号设置中生成新的恢复码。</p><RouterLink :to="{path:'/login',query:{username,next:route.query.next}}" class="inline-link">用新密码登录</RouterLink></div>
    <form v-else @submit.prevent="submit">
     <label>用户名<input v-model="username" aria-label="用户名" autocomplete="username" required minlength="3" maxlength="32" pattern="[A-Za-z0-9_]+" :disabled="busy" placeholder="输入用户名" autocapitalize="none" spellcheck="false"><small v-if="registering">3–32 个英文字母、数字或下划线</small></label>
     <label v-if="recovering">账号恢复码<input v-model="code" autocomplete="off" required minlength="20" maxlength="100" placeholder="粘贴保存的恢复码" :disabled="busy" autocapitalize="none" spellcheck="false"></label>
     <label>{{recovering?'新密码':'密码'}}<span class="entry-password"><input v-model="password" :aria-label="recovering?'新密码':'密码'" :type="visible?'text':'password'" :autocomplete="registering||recovering?'new-password':'current-password'" required minlength="12" maxlength="128" :disabled="busy" :placeholder="registering||recovering?'至少 12 个字符':'输入密码'"><button type="button" :aria-label="visible?'隐藏密码':'显示密码'" :aria-pressed="visible" @click="visible=!visible"><EyeOff v-if="visible" :size="19"/><Eye v-else :size="19"/></button></span><small v-if="registering||recovering">至少 12 个字符，建议使用密码管理器保存</small></label>
     <RouterLink v-if="!registering&&!recovering" :to="{path:'/recover',query:route.query.next?{next:route.query.next}:{}}" class="entry-forgot">忘记密码？</RouterLink>
     <label v-if="registering&&inviteRequired">内测邀请码（必填）<input v-model="inviteCode" required autocomplete="off" maxlength="100" :disabled="busy" placeholder="填写收到的邀请码"></label>
     <details v-else-if="registering" class="entry-invite"><summary>我有内测邀请码</summary><label>内测邀请码（选填）<input v-model="inviteCode" autocomplete="off" maxlength="100" :disabled="busy"></label></details>
     <p v-if="registering&&!registrationEnabled&&!createdUsername" class="error" role="alert">注册暂未开放。已有账号可以登录，或联系邀请你的管理员。</p>
     <p v-if="createdUsername&&error" class="hint">账号已经创建，点击“重试登录”即可继续，无需再次注册。</p><p v-if="error" class="error" role="alert">{{error}}</p><p v-if="!store.online" class="hint">当前离线，请联网后登录。已登录账号可以继续阅读缓存的内容。</p>
     <button class="primary entry-submit" :disabled="busy||!store.online||registering&&!registrationEnabled&&!createdUsername">{{busy?'正在处理…':recovering?'重设密码':registering?(createdUsername===username.trim()?'重试登录':'创建账号并进入'):'登录'}}</button>
    </form>
    <p v-if="!recovering&&(defaults?.text.enabled||defaults?.image.enabled)" class="entry-models"><Check :size="16" aria-hidden="true"/>默认模型已就绪，登录即可开始</p>
    <p v-if="recovering&&!recovered" class="entry-recovery-help">没有保存恢复码？联系邀请你的管理员核对账号。我们当前不提供邮件找回。</p>
    <section v-if="native" class="native-server divider"><label>后端服务器<input v-model="server" type="url" placeholder="https://你的私人服务"></label><button @click="configure">连接服务器</button><small>使用部署者允许且可达的 HTTPS 服务。</small></section>
    <p v-if="!configLoaded&&registering" class="entry-recovery-help">{{configFailed?'无法读取注册规则，请检查网络后重试。':'正在读取注册规则；提交时会由服务器核验邀请码和开放状态。'}}<button v-if="configFailed" class="text-action" @click="loadConfig">重新加载</button></p>
   </section>
  </div><footer class="entry-bottom">酒馆 · 内测版本 <RouterLink to="/data-use">数据与使用说明</RouterLink></footer>
 </div>
</template>
<style scoped>
.entry-page{min-height:100dvh;display:flex;flex-direction:column;color:var(--ink);background:var(--paper);padding:0 32px}
.entry-header{width:100%;max-width:1280px;margin:0 auto;display:flex;align-items:center;justify-content:space-between;padding:20px 0;gap:20px}
.entry-header>a{min-height:44px;display:inline-flex;align-items:center;color:var(--quiet);font-size:14px;text-decoration:none}
.entry-header .entry-brand{color:var(--accent);font-size:23px;font-weight:750;gap:10px}
.entry-layout{width:100%;max-width:420px;margin:32px auto;flex:1;display:flex;align-items:center}
.entry-form-card{width:100%;min-width:0;padding:0 28px 28px;background:var(--canvas);border:1px solid var(--edge);border-radius:14px;box-shadow:0 12px 40px #3421140a;overflow:hidden}
.entry-tabs{display:grid;grid-template-columns:1fr 1fr;margin:0 -28px 28px;border-bottom:1px solid var(--edge);background:var(--paper)}
.entry-tabs a{display:flex;align-items:center;justify-content:center;min-height:54px;padding:14px;text-decoration:none;color:var(--quiet);border-bottom:2px solid transparent;font-size:15px}
.entry-tabs .router-link-exact-active{background:var(--canvas);border-bottom-color:var(--accent);color:var(--accent);font-weight:650}
.entry-form-card>h1{font-family:var(--font-ui);font-size:24px;line-height:1.4;margin:0 0 8px;font-weight:700;letter-spacing:0}
.entry-description{font-size:14px;line-height:1.8;color:var(--quiet);margin:0 0 24px}
.entry-form-card label{font-size:14px;font-weight:500;margin:18px 0 0;display:grid;gap:8px}
.entry-form-card input{margin:0;border-radius:8px;border:1px solid var(--edge);min-height:48px;padding:12px;font-size:16px;width:100%;color:var(--ink);background:var(--canvas)}
.entry-form-card label small{font-size:12px;color:var(--quiet);font-weight:400;line-height:1.6}
.entry-password{display:flex;position:relative}.entry-password input{padding-right:52px}
.entry-password button{position:absolute;right:2px;top:2px;min-height:44px;min-width:44px;padding:10px;background:transparent;color:var(--quiet);border:0}
.entry-forgot{display:flex;align-items:center;justify-content:flex-end;min-height:44px;margin:4px 0 0;text-decoration:none;color:var(--accent);font-size:13px}
.entry-submit{width:100%;margin-top:20px;justify-content:center;min-height:48px;border-radius:8px;font-size:15px;font-weight:600}
.entry-models{display:flex;align-items:center;justify-content:center;gap:7px;color:var(--quiet);font-size:12px;line-height:1.7;margin:18px 0 0}.entry-models svg{color:var(--accent);flex:none}
.entry-bottom{display:flex;align-items:center;justify-content:center;flex-wrap:wrap;gap:16px;color:var(--quiet);font-size:12px;padding:20px 0}.entry-bottom a{display:inline-flex;align-items:center;min-height:44px;color:inherit}
.entry-back{display:inline-flex;align-items:center;gap:6px;min-height:44px;color:var(--quiet);font-size:13px;margin:12px 0 16px;text-decoration:none}
.entry-recovery-help{font-size:13px;line-height:1.8;color:var(--quiet);margin:18px 0 0}.entry-invite{font-size:13px;margin-top:18px;color:var(--quiet)}.entry-invite summary{cursor:pointer;min-height:44px;display:flex;align-items:center}
.entry-success{border:1px solid var(--edge);border-radius:10px;padding:20px;background:var(--wash)}.entry-success>svg{color:var(--accent)}.entry-success h3{margin:12px 0}.entry-success p{font-size:14px;line-height:1.8}
.entry-form-card .error,.entry-form-card .hint{font-size:13px;line-height:1.8;overflow-wrap:anywhere}
@media(max-width:767px){.entry-page{padding:0 20px}.entry-header{padding:12px 0}.entry-header .entry-brand{font-size:21px}.entry-layout{margin:20px auto;align-items:flex-start}.entry-form-card{padding:0 24px 24px}.entry-tabs{margin:0 -24px 24px}.entry-form-card>h1{font-size:23px}.entry-bottom{gap:10px;padding:8px 0 16px}}
@media(prefers-reduced-motion:reduce){.entry-page *{scroll-behavior:auto;transition:none}}
</style>
