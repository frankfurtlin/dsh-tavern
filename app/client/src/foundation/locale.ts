import {ref} from 'vue'
import {createLocaleController} from './vendor/i18n.js'
const zh={product:'账号与权益',account:'个人资料',wallet:'积分与会员',knowledge:'资料知识库',help:'使用帮助',admin:'运营后台',displayName:'显示名称',language:'界面语言',save:'保存',available:'可用积分',frozen:'冻结积分',redeem:'兑换权益',daily:'领取每日积分',ledger:'积分流水',orders:'我的订单',membership:'会员权益',refresh:'刷新',upload:'上传资料',search:'检索资料',download:'下载',remove:'删除',source:'来源版本',loading:'正在加载…'}
const en={product:'Account & benefits',account:'Profile',wallet:'Credits & membership',knowledge:'Knowledge library',help:'Help',admin:'Operations',displayName:'Display name',language:'Interface language',save:'Save',available:'Available credits',frozen:'Reserved credits',redeem:'Redeem benefits',daily:'Claim daily credits',ledger:'Credit ledger',orders:'My orders',membership:'Membership',refresh:'Refresh',upload:'Upload documents',search:'Search documents',download:'Download',remove:'Delete',source:'Source version',loading:'Loading…'}
let initial='zh-CN';try{initial=localStorage.getItem('ainovel.product.locale')||initial}catch{}
const controller=createLocaleController({loaders:{'zh-CN':()=>zh,en:()=>en},fallback:'zh-CN',initial,storage:localStorage,key:'ainovel.product.locale'})
export const language=ref(initial),messages=ref<Record<string,string>>(zh)
export async function switchLanguage(code:string){const result=await controller.switchLocale(code);if(result.applied){language.value=result.locale;messages.value=result.messages;if(location.pathname.startsWith('/product'))document.documentElement.lang=result.locale}}
void controller.ready.then(result=>{if(result.applied){language.value=result.locale;messages.value=result.messages}})
export function resetLanguage(){void switchLanguage('zh-CN')}
