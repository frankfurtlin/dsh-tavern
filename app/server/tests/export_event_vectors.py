"""Public deterministic cross-runtime vectors, without a provider call."""
import json
from copy import deepcopy
from pathlib import Path
from app.domain import canonical,digest
from app.prose.events import fold,fold_scoped
from tests.test_prose_event_state import relationship_story
from test_prose_event_ledger import fixture,ledger,proposal

normal=fixture()
gap=ledger(2,'林渡把蓝信再交给梅舟。梅舟从北岸去城中。',[
    proposal('item','林渡把蓝信再交给梅舟。',{'item':'蓝信','fromHolderId':'lin','toHolderId':'mei','location':None}),
    proposal('location','梅舟从北岸去城中。',{'from':'北岸','to':'城中'},'mei'),
],['c1:e1','c1:e2'])
past=ledger(2,'前夜。林渡把蓝信藏起来。',[
    proposal('time','前夜。',{'label':'前夜','relation':'before','relativeTo':'c1:e1'},None),
    proposal('item','林渡把蓝信藏起来。',{'item':'蓝信','fromHolderId':'lin','toHolderId':'lin','location':'木箱'}),
],['c1:e1','c1:e2'])
unicode=deepcopy(normal[:1]);unicode[0]['events'][0]['change']['item']='\ue000'
extra=deepcopy(unicode[0]['events'][0]);extra['id']='c1:e3';extra['change']['item']='🙂';unicode[0]['events'].append(extra)
prototype=deepcopy(normal[:1]);prototype[0]['events'][0]['change']['item']='__proto__'
vectors=[]
for name,values in [('five-chapter',normal),('soft-gap',[normal[0],gap]),('flashback',[normal[0],past]),('unicode-key-order',unicode),('literal-prototype-key',prototype),('directed-relationship-and-cause',relationship_story())]:
    state=fold(values);vectors.append({'name':name,'ledgers':values,'state':state,'canonical':canonical(state),'sha256':digest(canonical(state))})
next_batch=deepcopy(gap);next_batch['chapterIndex']=1
for i,entry in enumerate(next_batch['events']):entry['id']=f'c1:e{i+1}'
sources=[{'scope':'first','ledgers':normal[:1]},{'scope':'next','ledgers':[next_batch]}]
state=fold_scoped(sources);vectors.append({'name':'cross-batch-soft-transition','groups':sources,'state':state,'canonical':canonical(state),'sha256':digest(canonical(state))})
sources=[{'scope':'a@v1','ledgers':normal[:1]},{'scope':'b@v2','ledgers':relationship_story()}]
state=fold_scoped(sources);vectors.append({'name':'namespaced-multiple-batches','groups':sources,'state':state,'canonical':canonical(state),'sha256':digest(canonical(state))})
Path('/evidence/event-vectors.json').write_text(json.dumps(vectors,ensure_ascii=False,indent=2)+'\n','utf-8')
print({'vectors':len(vectors),'modelCalls':0})
