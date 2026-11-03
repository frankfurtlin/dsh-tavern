export interface Distribution{samples:number;medianMs:number|null;p95Ms:number|null}
export interface AnalyticsReport{
 generatedAt:string;timezone:string;range:{days:number;start:string;end:string};includeAdmins:boolean;unlimitedUsage:boolean;
 summary:{accounts:number;newAccounts:number;activeUsers:number;visitors:number;pageViews:number;sessions:number;engagedMs:number;tasks:number;successRate:number|null};
 trends:{day:string;users:number;pageViews:number;tasks:number;succeeded:number;failed:number}[];
 pages:{path:string;views:number;users:number}[];events:{name:string;count:number;users:number}[];
 funnel:{label:string;users:number;conversion:number|null}[];taskStates:Record<string,number>;errors:{code:string;count:number}[];
 timings:Record<'firstText'|'total'|'provider',Distribution>;clientLatency:Distribution;clientErrors:{path:string;status:number|null;code:string;count:number}[];
 usage:{calls:number;inputTokens:number;outputTokens:number;unknownTokenRecords:number;knownCostRecords:number;unknownCostRecords:number;costs:{currency:string;amount:string}[];models:({profileId:string;name:string;modelId:string|null;calls:number;inputTokens:number;outputTokens:number}&Record<string,unknown>)[]};
 images:{records:number;states:Record<string,number>;reusedAssets:number};works:{worldId:string;title:string;views:number;tasks:number;succeeded:number;failed:number}[];
 users:{id:string;username:string;role:string;lastSeenAt:string|null;tasks?:number;succeeded?:number;failed?:number}[];
 retention:{day:string;users:number;returned:number|null;d1:number|null}[];
 quality:{eventsTruncated:boolean;tasksTruncated:boolean;usageTruncated:boolean;imagesTruncated:boolean;clientEventsAreHints:boolean;costBasis:string;funnelBasis:string;retentionBasis:string}
}
export const number=(n:number|undefined)=>new Intl.NumberFormat('zh-CN').format(n||0)
export const percent=(n:number|null)=>n===null?'—':(n*100).toFixed(1)+'%'
export const seconds=(n:number|null)=>n===null?'—':(n/1000).toFixed(1)+' s'
export const eventLabels:Record<string,string>={'book.import.started':'开始导入小说','book.import.confirmed':'确认导入小说','author.saved':'保存作者配置','publication.submitted':'提交公开发布','chat.created':'创建世界外聊天','chat.sent':'提交世界外聊天','work.open':'打开作品','play.enter':'进入游玩','dialogue.open':'打开角色对话','dialogue.close':'结束角色对话','route.select':'选择地区路线','route.confirm':'确认前往地区','route.cancel':'取消前往地区','scene.ready':'360° 环境载入完成','scene.error':'360° 环境载入失败','action.submit':'提交行动已受理','model.saved':'保存模型选择','memory.saved':'保存记忆设置','page.engaged':'前台停留记录'}
