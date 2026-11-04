import type {RouteLocationNormalizedLoaded} from 'vue-router'

type Link={path:string;label:string}
export type PageLocation={title:string;area:Link;parent?:Link;ownBack?:boolean}
const explore={path:'/explore',label:'作品探索'},works={path:'/works',label:'我的作品'},characters={path:'/characters',label:'角色卡库'},creative={path:'/worlds/scripts',label:'创作工作台'}
const pages:Record<string,PageLocation>={
 '/data-use':{title:'数据与使用说明',area:explore,parent:explore},
 '/explore':{title:'作品探索',area:explore},'/works':{title:'我的作品',area:works},
 '/recent':{title:'最近游玩',area:{path:'/recent',label:'最近游玩'}},
 '/my-prompts':{title:'我的提示词',area:{path:'/my-prompts',label:'我的提示词'}},
 '/worlds/scripts':{title:'创作工作台',area:creative},
 '/characters':{title:'角色卡库',area:creative,parent:creative},
 '/characters/regex':{title:'输出替换规则',area:creative,parent:creative},
 '/characters/groups':{title:'群聊',area:creative,parent:creative},
 '/settings/import-history':{title:'导入旧版内容',area:explore,parent:explore},
 '/product/account':{title:'个人资料',area:explore,parent:explore},
 '/product/wallet':{title:'积分与会员',area:explore,parent:explore},
 '/product/admin':{title:'运营管理',area:explore,parent:explore},
 '/product/knowledge':{title:'参考资料',area:{path:'/product/knowledge',label:'参考资料'}},
 '/library':{title:'小说书架',area:works,parent:works},
 '/worlds':{title:'互动世界',area:works,parent:works},
 '/worlds/new':{title:'创建新开局',area:creative,parent:works},
 '/downloads':{title:'已下载',area:works,parent:works},
 '/insights':{title:'阅读洞察',area:works,parent:works},
 '/cases/blue-carbuncle':{title:'蓝宝石案',area:explore,parent:explore},
 '/features':{title:'功能与开放说明',area:explore,parent:explore},
}
export function pageLocation(route:Pick<RouteLocationNormalizedLoaded,'path'|'params'|'meta'>):PageLocation{
 const known=pages[route.path];if(known)return known
 if(/^\/conversations\/world\/[^/]+$/.test(route.path))return {title:'角色独立对话',area:characters,parent:characters}
 if(/^\/characters\/groups\/[^/]+$/.test(route.path))return {title:'群聊',area:creative,parent:creative}
 if(/^\/characters\/[^/]+$/.test(route.path))return {title:'角色卡详情',area:creative,parent:creative}
 const world=encodeURIComponent(String(route.params.worldId||''))
 if(/^\/worlds\/[^/]+\/author$/.test(route.path))return {title:'作者工作台',area:creative,parent:{path:'/stories/world-'+world,label:'作品详情'}}
 if(/^\/published\/[^/]+$/.test(route.path))return {title:'公开作品',area:explore,parent:explore}
 if(/^\/authors\/[^/]+$/.test(route.path))return {title:'作者主页',area:explore,parent:explore}
 if(/^\/stories\/[^/]+$/.test(route.path))return {title:'作品详情',area:works,parent:works,ownBack:true}
 if(/^\/worlds\/[^/]+\/lines$/.test(route.path))return {title:'故事线',area:works,parent:works,ownBack:true}
 if(/^\/books\/[^/]+\/(read|branches)$/.test(route.path))return {title:route.path.endsWith('/branches')?'故事时间线':'小说阅读',area:works,parent:works,ownBack:true}
 return {title:typeof route.meta.title==='string'?route.meta.title:'酒馆',area:explore,parent:explore}
}
