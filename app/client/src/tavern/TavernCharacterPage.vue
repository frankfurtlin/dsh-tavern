<script setup lang="ts">
import {computed,onMounted,onUnmounted,reactive,ref,watch} from 'vue'
import {useRoute,useRouter} from 'vue-router'
import {ArrowLeft,Download,MessageSquarePlus,Plus,Save,Trash2,Upload} from 'lucide-vue-next'
import {useApp} from '../stores/app'
import {useRequestScope} from '../platform/request-scope'
import {saveBlob,safeFileName} from '../platform/download'
import {
 characterAvatarBlob,createCharacterChat,deleteCharacter,deleteWorldInfo,exportCharacterBlob,
 getCharacter,importChats,listCharacterChats,listWorldInfo,previewWorldInfo,updateCharacter,
 updateWorldInfo,createWorldInfo,
 type TavernCharacter,TavernChat,WorldInfoEntry,WorldInfoInput,WorldInfoPosition,WorldInfoPreview,
} from './api'

const route=useRoute(),router=useRouter(),store=useApp(),scope=useRequestScope()
const cardId=computed(()=>String(route.params.id||''))
const card=ref<TavernCharacter|null>(null)
const chats=ref<TavernChat[]>([])
const worldInfo=ref<WorldInfoEntry[]>([])
const avatarUrl=ref('')
const loading=ref(true)
const error=ref('')
const saving=ref(false)
const saveError=ref('')
const saveNotice=ref('')
const exporting=ref(false)
const exportError=ref('')
const worldError=ref('')
const startingChat=ref(false)
const importingChats=ref(false)
const previewMessage=ref('')
const previewLoading=ref(false)
const previewError=ref('')
const previewResult=ref<WorldInfoPreview|null>(null)

const form=reactive({name:'',description:'',personality:'',scenario:'',firstMessage:'',systemPrompt:'',postHistoryInstructions:'',tagsText:''})
function fillForm(value:TavernCharacter){
  form.name=value.name||''
  form.description=value.description||''
  form.personality=value.personality||''
  form.scenario=value.scenario||''
  form.firstMessage=value.firstMessage||''
  form.systemPrompt=value.systemPrompt||''
  form.postHistoryInstructions=value.postHistoryInstructions||''
  form.tagsText=(value.tags||[]).join(', ')
}
function splitTags(value:string){return [...new Set(value.split(/[,，、\n]/).map(tag=>tag.trim()).filter(Boolean))].slice(0,20)}

async function load(){
  loading.value=true;error.value=''
  const ticket=scope()
  try{
    const value=await getCharacter(cardId.value)
    if(!ticket.valid())return
    card.value=value
    fillForm(value)
    if(value.hasAvatar){
      const blob=await characterAvatarBlob(cardId.value).catch(()=>null)
      if(blob&&ticket.valid())avatarUrl.value=URL.createObjectURL(blob)
    }
    const [chatResult,infoResult]=await Promise.allSettled([listCharacterChats(cardId.value),listWorldInfo(cardId.value)])
    if(!ticket.valid())return
    chats.value=chatResult.status==='fulfilled'?chatResult.value:[]
    if(infoResult.status==='fulfilled')worldInfo.value=infoResult.value
    else worldError.value=(infoResult.reason as Error)?.message||'世界书加载失败'
  }catch(e){
    if(ticket.valid())error.value=(e as Error).message||'角色卡加载失败，请稍后重试。'
  }finally{
    if(scope().valid())loading.value=false
  }
}

async function save(){
  if(!card.value||saving.value)return
  if(!form.name.trim()){saveError.value='请填写角色名。';return}
  saving.value=true;saveError.value='';saveNotice.value=''
  try{
    const value=await updateCharacter(cardId.value,{
      name:form.name.trim(),description:form.description,personality:form.personality,
      scenario:form.scenario,firstMessage:form.firstMessage,systemPrompt:form.systemPrompt,
      postHistoryInstructions:form.postHistoryInstructions,tags:splitTags(form.tagsText),
    })
    card.value={...card.value,...value}
    fillForm(card.value)
    saveNotice.value='角色设定已保存。'
  }catch(e){
    saveError.value=(e as Error).message||'保存失败，请稍后重试。'
  }finally{saving.value=false}
}

async function exportCard(){
  if(exporting.value)return
  exporting.value=true;exportError.value=''
  try{
    const blob=await exportCharacterBlob(cardId.value,'png')
    saveBlob(blob,safeFileName(card.value?.name||'character','character')+'.png')
    store.message('角色卡已导出。')
  }catch(e){
    exportError.value=(e as Error).message||'导出失败，请稍后重试。'
  }finally{exporting.value=false}
}

async function startChat(){
  if(startingChat.value)return
  startingChat.value=true
  try{
    const chat=await createCharacterChat(cardId.value)
    void router.push('/chats/'+encodeURIComponent(chat.id))
  }catch(e){
    saveError.value=(e as Error).message||'创建聊天失败，请稍后重试。'
  }finally{startingChat.value=false}
}

function openChat(chat:TavernChat){void router.push('/chats/'+encodeURIComponent(chat.id))}

const infoEditor=reactive({open:false,id:'',keys:'',content:'',position:'before_char' as WorldInfoPosition,depth:4,constant:false,probability:100,order:100,enabled:true})
function resetInfo(){infoEditor.open=false;infoEditor.id='';infoEditor.keys='';infoEditor.content='';infoEditor.position='before_char';infoEditor.depth=4;infoEditor.constant=false;infoEditor.probability=100;infoEditor.order=100;infoEditor.enabled=true}
function editInfo(entry:WorldInfoEntry){
  infoEditor.open=true;infoEditor.id=entry.id
  infoEditor.keys=[...(entry.keys||[])].join(', ')
  infoEditor.content=entry.content
  infoEditor.position=entry.position
  infoEditor.depth=entry.depth
  infoEditor.constant=entry.constant
  infoEditor.probability=entry.probability
  infoEditor.order=entry.order
  infoEditor.enabled=entry.enabled
}
async function saveInfo(){
  if(!infoEditor.content.trim()){worldError.value='请填写世界书内容。';return}
  worldError.value=''
  const body:WorldInfoInput={
    keys:infoEditor.keys.split(/[,，\n]/).map(key=>key.trim()).filter(Boolean),
    content:infoEditor.content,
    position:infoEditor.position,
    depth:infoEditor.depth,
    constant:infoEditor.constant,
    probability:infoEditor.probability,
    order:infoEditor.order,
    enabled:infoEditor.enabled,
  }
  try{
    if(infoEditor.id)await updateWorldInfo(cardId.value,infoEditor.id,body)
    else await createWorldInfo(cardId.value,body)
    worldInfo.value=await listWorldInfo(cardId.value)
    resetInfo()
  }catch(e){worldError.value=(e as Error).message||'保存世界书失败，请稍后重试。'}
}
async function runPreview(){
  if(previewLoading.value)return
  previewError.value='';previewResult.value=null
  previewLoading.value=true
  try{
    previewResult.value=await previewWorldInfo(cardId.value,previewMessage.value.trim())
  }catch(e){previewError.value=(e as Error).message||'注入预览失败，请稍后重试。'}
  finally{previewLoading.value=false}
}

async function importChat(event:Event){
  const input=event.target as HTMLInputElement
  const file=input.files?.[0]
  input.value=''
  if(!file||importingChats.value)return
  importingChats.value=true;worldError.value=''
  try{
    const chat=await importChats(cardId.value,file)
    chats.value=await listCharacterChats(cardId.value).catch(()=>chats.value)
    store.message('聊天记录已导入。')
    void router.push('/chats/'+encodeURIComponent(chat.id))
  }catch(e){worldError.value=(e as Error).message||'导入聊天失败，请检查 JSONL 文件。'}
  finally{importingChats.value=false}
}

async function removeInfo(entry:WorldInfoEntry){
  try{
    await deleteWorldInfo(cardId.value,entry.id)
    worldInfo.value=worldInfo.value.filter(item=>item.id!==entry.id)
  }catch(e){worldError.value=(e as Error).message||'删除世界书失败，请稍后重试。'}
}
async function removeCard(){
  if(!window.confirm('删除这张角色卡？聊天记录会一并隐藏。'))return
  try{
    await deleteCharacter(cardId.value)
    store.message('角色卡已删除。')
    void router.replace('/')
  }catch(e){saveError.value=(e as Error).message||'删除失败，请稍后重试。'}
}

watch(cardId,()=>void load())
onMounted(()=>void load())
onUnmounted(()=>{if(avatarUrl.value)URL.revokeObjectURL(avatarUrl.value)})
</script>

<template>
 <section class="tavern-character" aria-label="角色详情">
  <p v-if="error" class="error" role="alert">{{error}}<button class="inline-link" type="button" @click="load">重试</button></p>
  <p v-if="loading" class="tavern-loading" role="status">正在打开角色卡…</p>

  <template v-else-if="card">
   <header class="character-hero">
    <span class="character-avatar" aria-hidden="true">
     <img v-if="avatarUrl" :src="avatarUrl" alt="">
     <span v-else>{{(card.name||'?').slice(0,1)}}</span>
    </span>
    <div class="character-hero-copy">
     <h1>{{card.name}}</h1>
     <p class="character-tags">{{card.tags.length?card.tags.join(' · '):'未添加标签'}}<span v-if="card.chatCount"> · {{card.chatCount}} 次聊天</span></p>
    </div>
    <div class="character-hero-actions">
     <button type="button" class="primary" :disabled="startingChat" @click="startChat"><MessageSquarePlus :size="17"/>{{startingChat?'正在创建…':'开新聊天'}}</button>
     <button type="button" :disabled="exporting" @click="exportCard"><Download :size="17"/>{{exporting?'导出中…':'导出卡'}}</button>
    </div>
   </header>
   <p v-if="exportError" class="error" role="alert">{{exportError}}</p>

   <section class="character-section" aria-label="角色设定">
    <h2 class="section-title">角色设定</h2>
    <p v-if="saveError" class="error" role="alert">{{saveError}}</p>
    <p v-if="saveNotice" class="tavern-notice" role="status">{{saveNotice}}</p>
    <form class="character-form" @submit.prevent="save">
     <label>角色名<input v-model="form.name" aria-label="角色名" maxlength="80"></label>
     <label>简介<textarea v-model="form.description" rows="3" aria-label="角色简介"></textarea></label>
     <div class="form-pair">
      <label>性格<textarea v-model="form.personality" rows="3" aria-label="性格"></textarea></label>
      <label>场景<textarea v-model="form.scenario" rows="3" aria-label="场景"></textarea></label>
     </div>
     <label>开场白<textarea v-model="form.firstMessage" rows="3" aria-label="开场白"></textarea></label>
     <label>系统提示词<textarea v-model="form.systemPrompt" rows="3" aria-label="系统提示词"></textarea></label>
     <label>后置指令<textarea v-model="form.postHistoryInstructions" rows="2" aria-label="后置指令"></textarea></label>
     <label>标签（逗号分隔）<input v-model="form.tagsText" aria-label="标签"></label>
     <div class="form-actions">
      <button type="submit" class="primary" :disabled="saving"><Save :size="17"/>{{saving?'保存中…':'保存设定'}}</button>
      <button type="button" class="danger" @click="removeCard"><Trash2 :size="17"/>删除角色卡</button>
     </div>
    </form>
   </section>

   <section class="character-section" aria-label="示例对话">
    <h2 class="section-title">示例对话</h2>
    <pre v-if="card.exampleDialogue" class="example-dialogue">{{card.exampleDialogue}}</pre>
    <p v-else class="muted">这张角色卡没有示例对话。</p>
   </section>

   <section class="character-section" aria-label="世界书">
    <div class="section-head">
     <h2 class="section-title">世界书</h2>
     <button v-if="!infoEditor.open" type="button" @click="infoEditor.open=true"><Plus :size="16"/>新增条目</button>
    </div>
    <p v-if="worldError" class="error" role="alert">{{worldError}}</p>
    <p v-if="!worldInfo.length&&!infoEditor.open" class="muted">还没有世界书条目；导入卡里的 character_book 会自动出现在这里。</p>
    <ul v-else class="world-info-list">
     <li v-for="entry in worldInfo" :key="entry.id" class="world-info-item">
      <div class="world-info-copy">
       <p class="world-info-keys">{{(entry.keys||[]).join(', ')||'（关键词为空）'}}<span v-if="entry.constant" class="badge">常驻</span><span v-if="!entry.enabled" class="badge">已停用</span></p>
       <p class="world-info-text">{{entry.content}}</p>
       <p class="world-info-meta">{{entry.position}} · depth {{entry.depth}} · order {{entry.order}} · {{entry.probability}}% · {{entry.source==='import'?'导入':'手写'}}</p>
      </div>
      <div class="world-info-actions">
       <button type="button" @click="editInfo(entry)">编辑</button>
       <button type="button" class="danger" @click="removeInfo(entry)">删除</button>
      </div>
     </li>
    </ul>
    <div class="world-info-preview">
     <div class="section-head">
      <h3 class="preview-title">注入预览</h3>
      <button type="button" :aria-expanded="previewResult!==null" @click="previewResult=previewResult?null:previewResult">{{previewResult?'收起结果':'预览'}}</button>
     </div>
     <p class="muted">输入一句示例消息，看看哪些世界书条目会被命中。</p>
     <form class="preview-form" @submit.prevent="runPreview">
      <input v-model="previewMessage" aria-label="注入预览示例消息" placeholder="例如：外面开始下雨了" maxlength="400">
      <button type="submit" class="primary" :disabled="previewLoading">{{previewLoading?'预览中…':'注入预览'}}</button>
     </form>
     <p v-if="previewError" class="error" role="alert">{{previewError}}</p>
     <div v-if="previewResult" class="preview-result" role="status">
      <p class="preview-summary">命中 {{previewResult.matched?.length||0}} 条世界书</p>
      <ul v-if="previewResult.matched?.length" class="preview-match-list">
       <li v-for="entry in previewResult.matched" :key="entry.id">{{(entry.keys||[]).join(', ')||'（常驻）'}} · {{entry.content.slice(0,80)}}{{entry.content.length>80?'…':''}}</li>
      </ul>
      <p v-if="previewResult.injection" class="preview-injection-label">实际注入文本</p>
      <pre v-if="previewResult.injection" class="preview-injection">{{previewResult.injection}}</pre>
      <p v-else class="muted">这次没有命中任何世界书条目。</p>
     </div>
    </div>
    <form v-if="infoEditor.open" class="world-info-editor" @submit.prevent="saveInfo">
     <label>关键词（逗号分隔）<input v-model="infoEditor.keys" aria-label="世界书关键词"></label>
     <label>内容<textarea v-model="infoEditor.content" rows="3" aria-label="世界书内容"></textarea></label>
     <div class="form-pair">
      <label>位置<select v-model="infoEditor.position" aria-label="世界书位置"><option value="before_char">角色前</option><option value="after_char">角色后</option><option value="before_example">示例前</option><option value="at_depth">指定深度</option></select></label>
      <label>深度<input v-model.number="infoEditor.depth" type="number" min="0" aria-label="世界书深度"></label>
     </div>
     <div class="form-pair">
      <label>概率<input v-model.number="infoEditor.probability" type="number" min="0" max="100" aria-label="世界书概率"></label>
      <label>顺序<input v-model.number="infoEditor.order" type="number" aria-label="世界书顺序"></label>
     </div>
     <label class="check"><input v-model="infoEditor.constant" type="checkbox"> 常驻（不依赖关键词）</label>
     <label class="check"><input v-model="infoEditor.enabled" type="checkbox"> 启用</label>
     <div class="form-actions">
      <button type="submit" class="primary">保存条目</button>
      <button type="button" @click="resetInfo">取消</button>
     </div>
    </form>
   </section>

   <section class="character-section" aria-label="聊天历史">
    <div class="section-head">
     <h2 class="section-title">聊天历史</h2>
     <div class="history-actions">
      <label class="import-chat-button" :class="{disabled:importingChats}">
       <Upload :size="16"/>{{importingChats?'导入中…':'导入酒馆聊天'}}
       <input type="file" accept=".jsonl,.json,application/jsonl,application/json" aria-label="导入酒馆聊天 JSONL" :disabled="importingChats" @change="importChat">
      </label>
      <button type="button" class="primary" :disabled="startingChat" @click="startChat"><MessageSquarePlus :size="16"/>开新聊天</button>
     </div>
    </div>
    <p v-if="!chats.length" class="muted">还没有聊天记录，开一段新对话吧。</p>
    <ul v-else class="chat-history">
     <li v-for="chat in chats" :key="chat.id">
      <button type="button" class="chat-history-item" @click="openChat(chat)">
       <span class="chat-history-title">{{chat.title||'未命名聊天'}}</span>
       <span class="chat-history-meta">{{chat.lastMessageAt||chat.updatedAt?new Date(chat.lastMessageAt||chat.updatedAt||'').toLocaleString('zh-CN'):''}}</span>
      </button>
     </li>
    </ul>
   </section>
  </template>
 </section>
</template>

<style scoped>
.tavern-character{width:100%;max-width:820px;margin:0 auto;padding:20px clamp(14px,3vw,28px) 72px;display:flex;flex-direction:column;gap:26px}
.tavern-loading{color:var(--quiet);text-align:center;padding:48px 0}
.character-hero{display:flex;align-items:center;gap:16px;flex-wrap:wrap}
.character-avatar{width:84px;height:84px;flex:none;border-radius:20px;overflow:hidden;display:flex;align-items:center;justify-content:center;background:var(--wash);color:var(--accent);font-family:var(--font-serif);font-size:34px}
.character-avatar img{width:100%;height:100%;object-fit:cover}
.character-hero-copy{flex:1;min-width:180px}.character-hero-copy h1{margin:0 0 4px;font-size:26px;font-family:var(--font-serif)}
.character-tags{margin:0;color:var(--quiet);font-size:13px}
.character-hero-actions{display:flex;gap:8px;flex-wrap:wrap}
.character-section{border-top:1px solid var(--edge);padding-top:22px}
.section-head{display:flex;align-items:center;justify-content:space-between;gap:12px;margin-bottom:12px}
.section-title{font-size:17px;margin:0 0 10px;font-family:var(--font-serif)}
.section-head .section-title{margin:0}
.character-form{display:flex;flex-direction:column;gap:12px}
.character-form label,.world-info-editor label{display:flex;flex-direction:column;gap:6px;font-size:13px;color:var(--quiet)}
.form-pair{display:grid;grid-template-columns:1fr 1fr;gap:12px}
.form-actions{display:flex;gap:10px;flex-wrap:wrap;margin-top:4px}
.form-actions button{display:flex;align-items:center;gap:6px}
.muted{color:var(--quiet);font-size:14px}
.tavern-notice{color:var(--accent);font-size:14px;margin:0 0 8px}
.example-dialogue{white-space:pre-wrap;font-family:inherit;background:var(--wash);border-radius:12px;padding:14px;line-height:1.7;font-size:14px;margin:0}
.world-info-list{list-style:none;margin:0;padding:0;display:flex;flex-direction:column;gap:10px}
.world-info-item{display:flex;gap:12px;justify-content:space-between;border:1px solid var(--edge);border-radius:12px;padding:12px}
.world-info-copy{min-width:0}.world-info-keys{margin:0 0 4px;font-size:13px;font-weight:600;color:var(--ink);display:flex;flex-wrap:wrap;gap:6px;align-items:center}
.world-info-text{margin:0 0 4px;font-size:14px;line-height:1.6;white-space:pre-wrap;overflow-wrap:anywhere}
.world-info-meta{margin:0;font-size:12px;color:var(--quiet)}
.world-info-actions{display:flex;flex-direction:column;gap:6px;flex:none}
.world-info-editor{display:flex;flex-direction:column;gap:12px;margin-top:14px;padding:14px;border:1px dashed var(--edge);border-radius:12px}
.check{flex-direction:row!important;align-items:center;gap:8px!important}
.world-info-preview{margin-top:16px;padding:14px;border:1px dashed var(--edge);border-radius:12px;display:flex;flex-direction:column;gap:10px}
.preview-title{margin:0;font-size:15px;font-family:var(--font-serif)}
.preview-form{display:flex;gap:8px}
.preview-form input{flex:1;min-height:40px}
.preview-form button{min-height:40px;white-space:nowrap}
.preview-result{display:flex;flex-direction:column;gap:8px;padding:12px;border-radius:10px;background:var(--wash)}
.preview-summary{margin:0;font-size:13px;color:var(--quiet)}
.preview-match-list{margin:0;padding-left:18px;font-size:13px;line-height:1.7;color:var(--ink)}
.preview-injection-label{margin:0;font-size:12px;color:var(--quiet)}
.preview-injection{margin:0;white-space:pre-wrap;font-size:13px;line-height:1.7;background:var(--canvas);border:1px solid var(--edge);border-radius:8px;padding:10px;max-height:220px;overflow:auto}
.history-actions{display:flex;gap:8px;flex-wrap:wrap}
.import-chat-button{display:inline-flex;align-items:center;gap:6px;min-height:44px;padding:0 14px;border:1px solid var(--edge);border-radius:10px;background:var(--canvas);color:var(--ink);font-size:14px;cursor:pointer}
.import-chat-button input{display:none}
.import-chat-button.disabled{opacity:.55;pointer-events:none}
.inline-link{min-height:0;padding:0 6px;border:0;background:transparent;color:var(--accent);text-decoration:underline;cursor:pointer}
.chat-history{list-style:none;margin:0;padding:0;display:flex;flex-direction:column;gap:8px}
.chat-history-item{width:100%;display:flex;justify-content:space-between;gap:12px;padding:12px 14px;border:1px solid var(--edge);border-radius:12px;background:var(--canvas);cursor:pointer;text-align:left}
.chat-history-item:hover{border-color:var(--accent)}
.chat-history-title{font-size:15px;color:var(--ink);overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.chat-history-meta{font-size:12px;color:var(--quiet);flex:none}
@media(max-width:620px){.form-pair{grid-template-columns:1fr}.character-hero-actions{width:100%}.character-hero-actions button{flex:1;justify-content:center}}
</style>
