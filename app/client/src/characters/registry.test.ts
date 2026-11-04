import {it,expect} from 'vitest'
import {workspaceGroups} from '../workspace/navigation'
import {capabilities} from '../workspace/capabilities'

it('puts the character library in the creation group of the sidebar',()=>{
 const creation=workspaceGroups.find(group=>group.name==='创作')
 const entry=creation?.items.find(item=>item.name==='角色卡库')
 expect(entry?.path).toBe('/characters')
 expect(entry?.icon).toBeTruthy()
})

it('marks the standalone role-card capability as partially available',()=>{
 const entry=capabilities.find(capability=>capability.id==='role-presets')
 expect(entry?.status).toBe('partial')
 expect(entry?.detail).toBe('角色卡导入、单卡聊天、世界书与预设/正则替换已可用；群聊与插件库不再提供。')
 expect(entry?.path).toBe('/characters')
})
