"""Fixture registration and finite after-commit response barriers, not fake DTOs."""
import os,sys,json,hashlib,threading,time
from pathlib import Path
from sqlalchemy import select,func,text
from app.config import settings
from app.db import engine,SessionLocal
from app import models as m
from app.providers import MockAdapter,ChatCompletionsAdapter

PROJECT=os.getenv('FIXTURE_PROJECT')
allowed={'reader-world-next-m6-browser-20261005':'reader_portable_browser_test','reader-world-next-m6-browser-phase2-20261005':'reader_portable_browser_phase2_test'}
assert PROJECT in allowed and engine.url.host=='db' and engine.url.database==allowed[PROJECT]
assert settings.ai_mode=='mock' and settings.storage_path==Path('/data/private')
ATTEMPTS=Path('/data/private/provider-attempts.json')
def forbidden(*args,**kwargs):
    ATTEMPTS.write_text(json.dumps({'attempts':1}),encoding='utf-8');raise RuntimeError('All model/provider calls forbidden in this fixture')
for adapter in (MockAdapter,ChatCompletionsAdapter):
    adapter.generate=forbidden;adapter.generate_stream=forbidden

def init():
    from alembic import command
    from alembic.config import Config
    from app.world.migrate import migrate
    from app.assets.models import AssetsBase,ASSET_TABLES
    from app.portable.models import PortableBase,PORTABLE_TABLES
    command.upgrade(Config('alembic.ini'),'0008');migrate()
    AssetsBase.metadata.create_all(engine,tables=ASSET_TABLES)
    PortableBase.metadata.create_all(engine,tables=PORTABLE_TABLES)
    ATTEMPTS.parent.mkdir(parents=True,exist_ok=True);ATTEMPTS.write_text('{"attempts":0}',encoding='utf-8')

def runtime():
    from app.assets.models import ImageAsset,AssetVariant,AssetPlacement
    from app.portable.models import PortableOperation
    with SessionLocal() as db:
        count=lambda cls:db.scalar(select(func.count()).select_from(cls))
        active=db.scalar(select(func.count()).select_from(m.User).where(m.User.status=='active'))
        queue=db.scalar(select(func.count()).select_from(m.Task).where((m.Task.state.in_(['queued','running']))|((m.Task.state=='failed')&(m.Task.persistence_state=='recoverable'))))
        value={'project':PROJECT,'database':engine.url.database,'aiMode':settings.ai_mode,'activeAccounts':active,'accounts':count(m.User),'books':count(m.Book),
               'tasks':count(m.Task),'usage':count(m.UsageRecord),'profiles':count(m.ModelProfile),'nonterminalOrRecoverable':queue,
               'providerAttempts':json.loads(ATTEMPTS.read_text('utf-8'))['attempts'],'assetCount':count(ImageAsset),'variantCount':count(AssetVariant),
               'placementCount':count(AssetPlacement),'operations':count(PortableOperation),'historicalRoleMessages':count(m.RoleMessage)}
        value['booksPublic']=[{'id':row.id,'owner':row.owner_id,'title':row.title,'status':row.status} for row in db.scalars(select(m.Book))]
        value['assetsPublic']=[{'id':row.id,'owner':row.owner_id,'sha256':row.sha256,'purpose':row.purpose} for row in db.scalars(select(ImageAsset))]
        value['variantHashes']=[{'assetId':row.asset_id,'name':row.name,'sha256':row.sha256,'actualSha256':hashlib.sha256(row.data).hexdigest()} for row in db.scalars(select(AssetVariant))]
        assert all(row['actualSha256']==row['sha256'] for row in value['variantHashes'])
    source=Path('/artifacts/isolation.json');expected=json.loads(source.read_text('utf-8'))['sourceHashes'];value['sourceHashes']={}
    for key,expected_hash in expected.items():
        if key.startswith('server/'):
            actual=hashlib.sha256((Path('/app')/key[7:]).read_bytes()).hexdigest();assert actual==expected_hash,key;value['sourceHashes'][key]=actual
    print(json.dumps(value,ensure_ascii=True))

if __name__=='__main__' and len(sys.argv)>1:
    if sys.argv[1]=='--init':init()
    elif sys.argv[1]=='--runtime':runtime()
    else:raise RuntimeError('Unknown owned helper mode')
    raise SystemExit(0)

from fastapi import Depends
from pydantic import BaseModel
from app.main import app
from app.security import current_user
from app.db import get_db
from app.errors import Problem
from app.assets.api import router as asset_router
from app.portable.api import router as portable_router

barriers={};lock=threading.Lock()
def own(user):
    if not user.username.startswith('pbu_'):raise Problem('RESOURCE_NOT_FOUND','Fixture account not allowed',404)
    return user.id
class SeamInput(BaseModel):
    phase:str
class HistoryInput(BaseModel):
    bookId:str
@app.post('/api/reader/v1/__fixture/history')
def history(data:HistoryInput,user=Depends(current_user),db=Depends(get_db)):
    owner=own(user)
    assert len(data.bookId)==32
    book=db.scalar(select(m.Book).where(m.Book.id==data.bookId,m.Book.owner_id==owner,m.Book.status=='active'))
    assert book and db.scalar(select(func.count()).select_from(m.Task).where(m.Task.owner_id==owner))==0
    source=db.get(m.SourceDocument,book.source_id)
    binding=source.source_map['characterBindings'][0]
    block=db.get(m.ContentBlock,binding['blockId']);assert block.book_id==book.id
    from app.domain import make_anchor
    anchor=make_anchor(book.id,book.original_branch_id,book.original_revision_id,block,len(block.text.encode('utf-16-le'))//2)
    snapshot=m.KnowledgeSnapshot(id=m.uid(),owner_id=owner,book_id=book.id,branch_id=book.original_branch_id,revision_id=book.original_revision_id,boundary=anchor,scope_hash=hashlib.sha256(b'own authored portable history').hexdigest(),profile_version='fixture-terminal-history',completeness='complete',data={'characters':[{'id':'portable-history','name':binding['name'],'displayName':binding['name'],'identityProofVersion':2,'evidenceIds':['e1']}],'evidence':[{'id':'e1','quote':block.text,'anchor':anchor}]})
    session=m.RoleSession(id=m.uid(),owner_id=owner,book_id=book.id,branch_id=book.original_branch_id,pinned_revision_id=book.original_revision_id,pinned_anchor=anchor,character_id='portable-history',character_name=binding['name'],character_identity_id=binding['identityId'],snapshot_id=snapshot.id,follow_progress=False)
    task=m.Task(id=m.uid(),owner_id=owner,task_type='role_chat',state='succeeded',persistence_state='saved',provider_state='completed',usage_state='known',result_outcome='complete',payload={'bookId':book.id,'sessionId':session.id,'anchor':anchor},result={'fixtureHistory':True})
    db.add_all([snapshot,session,task]);db.flush()
    db.add(m.RoleMessage(owner_id=owner,session_id=session.id,turn_id='authored-portable-turn',sequence=1,role='assistant',text='这是自编的备份历史夹具：请把信留在灯下。',task_id=task.id,status='saved'));db.commit()
    return {'bookId':book.id,'sessionId':session.id,'taskId':task.id,'historicalFixtureRows':1,'newModelCalls':0,'anchor':anchor}
@app.get('/api/reader/v1/__fixture/snapshot')
def snapshot(user=Depends(current_user),db=Depends(get_db)):
    owner=own(user)
    from app.assets.models import ImageAsset,AssetVariant,AssetPlacement
    from app.portable.models import PortableOperation
    classes=[m.Book,m.Task,m.RoleSession,m.RoleMessage,m.UsageRecord,m.ModelProfile,ImageAsset,AssetPlacement,PortableOperation]
    counts={cls.__tablename__:db.scalar(select(func.count()).select_from(cls).where(cls.owner_id==owner)) for cls in classes}
    books=list(db.scalars(select(m.Book).where(m.Book.owner_id==owner)))
    assets=list(db.scalars(select(ImageAsset).where(ImageAsset.owner_id==owner)))
    variants=list(db.scalars(select(AssetVariant).where(AssetVariant.asset_id.in_([asset.id for asset in assets]))))
    return {'ownerId':owner,'counts':counts,'providerAttempts':json.loads(ATTEMPTS.read_text())['attempts'],'books':[{'id':b.id,'title':b.title,'branchId':b.original_branch_id,'revisionId':b.original_revision_id,'status':b.status} for b in books],
        'assets':[{'id':a.id,'purpose':a.purpose,'sha256':a.sha256,'sourceKind':a.source_kind} for a in assets],
        'variants':[{'assetId':v.asset_id,'name':v.name,'sha256':v.sha256,'actualSha256':hashlib.sha256(v.data).hexdigest(),'bytes':len(v.data)} for v in variants],
        'operations':[{'id':v.id,'kind':v.kind,'status':v.status,'version':v.version,'requestKey':v.request_key} for v in db.scalars(select(PortableOperation).where(PortableOperation.owner_id==owner))]}
@app.post('/api/reader/v1/__fixture/response-seam')
def arm(data:SeamInput,user=Depends(current_user)):
    owner=own(user)
    if data.phase not in {'export','prepare','restore','status'}:raise Problem('INVALID_REQUEST','Unknown seam')
    with lock:
        if owner in barriers and not barriers[owner]['event'].is_set():raise Problem('INVALID_REQUEST','One seam at a time')
        barriers[owner]={'phase':data.phase,'event':threading.Event(),'observed':False,'operationId':None}
    return {'armed':True,'phase':data.phase}
@app.get('/api/reader/v1/__fixture/response-seam')
def observe(user=Depends(current_user)):
    owner=own(user)
    with lock:
        value=barriers.get(owner)
        return {'armed':value is not None,'observed':bool(value and value['observed']),'phase':value['phase'] if value else None,'operationId':value['operationId'] if value else None}
@app.post('/api/reader/v1/__fixture/response-release')
def release(user=Depends(current_user)):
    owner=own(user)
    with lock:
        if owner in barriers:barriers[owner]['event'].set()
    return {'released':True}
def wrap(route,phase):
    original=route.dependant.call
    def call(**kwargs):
        value=original(**kwargs)
        owner=kwargs['user'].id
        with lock:
            gate=barriers.get(owner)
            if gate and gate['phase']==phase and not gate['observed']:
                gate['observed']=True;gate['operationId']=value.get('id') if isinstance(value,dict) else None
            else:gate=None
        if gate:gate['event'].wait(30)
        return value
    route.dependant.call=call
app.include_router(asset_router,prefix='/api/reader/v1')
app.include_router(portable_router,prefix='/api/reader/v1')
for route in app.routes:
    phase={'/api/reader/v1/stories/{story_id}/backup':'export','/api/reader/v1/backups/prepare':'prepare','/api/reader/v1/backups/{operation_id}/restore':'restore','/api/reader/v1/backups/{operation_id}':'status'}.get(route.path)
    if phase:wrap(route,phase)
