import type {PlotCheck} from './types'
export const plotId=(kind:string)=>kind+'-'+crypto.randomUUID().replaceAll('-','').slice(0,12)
export const plotCheck=():PlotCheck=>({kind:'field',entityId:'player',placeId:null,itemId:null,quantity:1,loreId:null,toId:null,field:'author_decision',value:'完成'})
