import {Compass,MessagesSquare,UserRound,NotebookPen,SlidersHorizontal,Database,HelpCircle,Clock3,Feather,MessageSquareText,Home,Library,Users} from 'lucide-vue-next'
export const workspaceNavigation=[{path:'/explore',name:'探索',icon:Home},{path:'/works',name:'作品',icon:Library},{path:'/recent',name:'最近',icon:Clock3},{path:'/worlds/scripts',name:'创作',icon:Feather},{path:'/settings',name:'我的',icon:UserRound}]
export const workspaceGroups=[
 {name:'故事与阅读',items:[{path:'/explore',name:'探索作品',icon:Compass},{path:'/works',name:'我的作品',icon:Library},{path:'/recent',name:'最近体验',icon:Clock3}]},
 {name:'创作',items:[{path:'/worlds/scripts',name:'创作工作台',icon:Feather},{path:'/characters',name:'角色卡库',icon:Users},{path:'/characters/groups',name:'群聊',icon:MessagesSquare},{path:'/my-prompts',name:'我的提示词',icon:MessageSquareText},{path:'/product/knowledge',name:'参考资料',icon:Database}]},
 {name:'我的',items:[{path:'/conversations',name:'我的对话',icon:MessagesSquare},{path:'/notebook',name:'我的笔记',icon:NotebookPen},{path:'/settings/models',name:'模型与用量',icon:SlidersHorizontal},{path:'/settings',name:'账号设置',icon:UserRound},{path:'/tasks/activity',name:'任务进度',icon:Clock3},{path:'/help/feedback',name:'反馈与通知',icon:MessagesSquare},{path:'/help',name:'使用帮助',icon:HelpCircle}]},
]
