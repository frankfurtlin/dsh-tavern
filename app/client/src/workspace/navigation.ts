import {Compass,Home,Users} from 'lucide-vue-next'

export const workspaceNavigation=[{path:'/explore',name:'探索',icon:Home},{path:'/characters',name:'角色卡库',icon:Users}]

export const workspaceGroups=[
 {name:'故事与阅读',items:[{path:'/explore',name:'探索作品',icon:Compass}]},
 {name:'创作',items:[{path:'/characters',name:'角色卡库',icon:Users}]},
]
