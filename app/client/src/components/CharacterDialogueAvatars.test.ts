import {it,expect,vi,afterEach} from 'vitest'
import {ref} from 'vue'
import SceneReply from '../world/SceneReply.vue'
import CharacterAvatar from './CharacterAvatar.vue'
import {characterAppearanceKey} from '../world/character-appearance'
import {compileRender,renderer,node,descendants,provideSfc,flush} from '../test/host-renderer'
vi.mock('./PublicStreamText.vue',()=>({default:{render:()=>null}}))
vi.mock('../api',()=>({authorizationHeaders:()=>({})}))
vi.mock('../platform/bridge',()=>({backendBase:()=>''}))
vi.mock('../platform/request-scope',()=>({useRequestScope:()=>()=>({valid:()=>true})}))
compileRender(SceneReply,new URL('../world/SceneReply.vue',import.meta.url))
compileRender(CharacterAvatar,new URL('./CharacterAvatar.vue',import.meta.url))
let app:ReturnType<typeof renderer.createApp>|undefined
afterEach(()=>app?.unmount())
it('attributes avatars by explicit speaker ID and leaves narration and unknown speakers without a role avatar',async()=>{
 const root=node('root'),avatars=ref({holmes:'blob:holmes',watson:'blob:watson',ryder:'blob:ryder'})
 app=renderer.createApp(SceneReply,{playerId:'watson',user:'我问莱德的下落。',segments:[
  {kind:'speech',speakerId:'holmes',speakerName:'福尔摩斯',text:'华生，我们先听莱德说明。'},
  {kind:'narration',text:'莱德听见福尔摩斯的声音，低下了头。'},
  {kind:'speech',speakerId:'ryder',speakerName:'莱德',text:'我愿意说明。'},
  {kind:'speech',text:'没有确认来源的旧台词。'}]})
 provideSfc(app);app.provide(characterAppearanceKey,{avatars,open:()=>{}});app.mount(root);await flush()
 expect(descendants(root,n=>n.tag==='img').map(n=>n.props.src)).toEqual(['blob:watson','blob:holmes','blob:ryder'])
 avatars.value={...avatars.value,holmes:'blob:holmes-version-2'};await flush()
 expect(descendants(root,n=>n.tag==='img').map(n=>n.props.src)).toEqual(['blob:watson','blob:holmes-version-2','blob:ryder'])
})
