import {it,expect} from 'vitest'
import {defineComponent,h,nextTick,type WritableComputedRef} from 'vue'
import {createRouter,createMemoryHistory} from 'vue-router'
import {renderer,node,flush} from '../test/host-renderer'
import {queryField} from './query-state'

it('coalesces multiple collection filters and restores them through detail navigation and browser back',async()=>{
 const router=createRouter({history:createMemoryHistory(),routes:[{path:'/works',component:{render:()=>null}},{path:'/detail',component:{render:()=>null}}]})
 await router.push('/works?q=old&kind=world');await router.isReady()
 let q!:WritableComputedRef<string>,kind!:WritableComputedRef<string>
 const component=defineComponent({setup(){q=queryField('q');kind=queryField('kind');return()=>h('div',q.value+kind.value)}})
 const app=renderer.createApp(component);app.use(router);app.mount(node('root'))
 expect(q.value).toBe('old');q.value='新的关键词';kind.value='book';await flush();await nextTick()
 expect(router.currentRoute.value.query).toMatchObject({q:'新的关键词',kind:'book'})
 await router.push('/detail');router.back();await flush()
 expect(q.value).toBe('新的关键词');expect(kind.value).toBe('book')
 q.value='';kind.value='';await flush();expect(router.currentRoute.value.query).toEqual({})
 app.unmount()
})
