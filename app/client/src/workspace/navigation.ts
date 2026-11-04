import {Compass,MessagesSquare,Clock3,Feather,MessageSquareText,Home,Library,Users,Database} from 'lucide-vue-next'
export const workspaceNavigation=[{path:'/explore',name:'探索',icon:Home},{path:'/works',name:'作品',icon:Library},{path:'/recent',name:'最近',icon:Clock3},{path:'/worlds/scripts',name:'创作',icon:Feather}]
export const workspaceGroups=[
 {name:'故事与阅读',items:[{path:'/explore',name:'探索作品',icon:Compass},{path:'/works',name:'我的作品',icon:Library},{path:'/recent',name:'最近体验',icon:Clock3}]},
 {name:'创作',items:[{path:'/worlds/scripts',name:'创作工作台',icon:Feather},{path:'/characters',name:'角色卡库',icon:Users},{path:'/characters/groups',name:'群聊',icon:MessagesSquare},{path:'/my-prompts',name:'我的提示词',icon:MessageSquareText},{path:'/product/knowledge',name:'参考资料',icon:Database}]},
]
