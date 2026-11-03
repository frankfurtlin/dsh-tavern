import {it,expect,vi,afterEach} from 'vitest'
import {reactive,defineComponent,h} from 'vue'
import PublicStreamText from './PublicStreamText.vue'
import {compileRender,renderer,node,provideSfc,text,flush,type Host} from '../test/host-renderer'
compileRender(PublicStreamText,new URL('./PublicStreamText.vue',import.meta.url))
let app:ReturnType<typeof renderer.createApp>|undefined,root:Host
afterEach(()=>{app?.unmount();app=undefined;vi.useRealTimers()})
it('actual component batches bursts, resets a replaced epoch and cancels queued old flush',async()=>{
  vi.useFakeTimers();const props=reactive({text:'你😀',contentId:'c',streamVersion:1});root=node('root');app=renderer.createApp(defineComponent({setup:()=>()=>h(PublicStreamText,{...props})}));provideSfc(app);app.mount(root);await flush();expect(text(root)).toBe('你😀')
  props.text+='第一段';props.text+='第二段';await flush();expect(text(root)).toBe('你😀')
  await vi.advanceTimersByTimeAsync(16);await flush();expect(text(root)).toBe('你😀第一段第二段')
  props.text+='旧排队片段';props.streamVersion=2;props.text='新版本';await flush();await vi.advanceTimersByTimeAsync(30);expect(text(root)).toBe('新版本')
  props.text+='卸载前排队';app.unmount();app=undefined;await vi.advanceTimersByTimeAsync(30);expect(text(root)).not.toContain('卸载前排队')
})
