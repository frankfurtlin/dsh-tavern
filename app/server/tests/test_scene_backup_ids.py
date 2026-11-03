from types import SimpleNamespace
from copy import deepcopy
from app.portable.world_ids import remap_world_local_ids

def test_scene_chat_presence_and_return_routes_remap_only_declared_ids():
    state={'playerId':'reader','location':'room','entities':{'reader':{'location':'room'},'npc':{'location':'room'},'room':{'location':None},'garden':{'location':None}},'unresolved':[]}
    definition={'location':'room','entities':[{'id':key,'type':'character' if key in ('reader','npc') else 'place'} for key in state['entities']],'initial':deepcopy(state['entities']),'rules':{},'lore':[]}
    memory={'sceneConversation':{'playerId':'reader','entries':[{'partnerId':'npc','user':'npc is a literal string','reply':'room','turnId':'global-turn','checkpointId':'global-cp'}]},'scenePresence':{'playerId':'reader','location':'room','absentCharacterIds':['npc']},'sceneSpatialContext':{'playerId':'reader','location':'room','absentCharacterIds':['npc'],'text':'room'},'sceneObservations':{'playerId':'reader','location':'room','items':[]},'sceneConnections':[{'from':'room','to':'garden'}]}
    world=SimpleNamespace(definition=definition);cp=SimpleNamespace(world_id='new-world',definition=deepcopy(definition),state=state,changes=[],memory=memory)
    mappings={('world','old-world'):'new-world'}
    remap_world_local_ids({'world':{'new-world':world},'checkpoint':{'new-cp':cp}},mappings)
    ids={key:mappings[('world_entity','old-world:'+key)] for key in ('reader','npc','room','garden')}
    assert cp.memory['sceneConversation']['entries'][0]['partnerId']==ids['npc']
    assert cp.memory['sceneConversation']['entries'][0]['user']=='npc is a literal string'
    assert cp.memory['sceneConversation']['entries'][0]['reply']=='room'
    assert cp.memory['scenePresence']['location']==ids['room']
    assert cp.memory['scenePresence']['absentCharacterIds']==[ids['npc']]
    assert cp.memory['sceneSpatialContext']['text']=='room'
    assert cp.memory['sceneConnections']==[{'from':ids['room'],'to':ids['garden']}]
