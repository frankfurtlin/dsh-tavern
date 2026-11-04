from copy import deepcopy
from fastapi import APIRouter, Depends, Request, Query
from fastapi.responses import Response
from sqlalchemy import select, func, delete
from ..db import get_db
from ..domain import owned, idempotent
from ..models import User, uid, now
from ..security import current_user
from ..errors import Problem
from ..foundation.core import require_admin, account
from ..world.api import available
from ..world.models import World
from .models import AuthorDraft, Publication, PublicFavorite, PublicReport
from .schemas import SaveAuthor, SubmitPublication, VersionInput, ReviewPublication, ReportInput, FavoriteInput
from .service import read_draft, save_draft, publication_owner, compile_definition

router=APIRouter(prefix='/authoring',dependencies=[Depends(available)],tags=['authoring-and-publication'])


def mutate(db,user,request,scope,payload,action):
    return idempotent(db,user.id,'authoring:'+scope,request.headers.get('idempotency-key'),payload,action)


def live_query():
    return select(Publication).join(User,User.id==Publication.owner_id).join(World,World.id==Publication.world_id).where(
        Publication.live.is_(True),User.status=='active',World.status=='active')


def live_publication(db,identity,lock=False):
    query=live_query().where(Publication.id==identity)
    row=db.scalar(query.with_for_update(of=Publication) if lock else query)
    if not row:raise Problem('PUBLICATION_UNAVAILABLE','作品已下架或暂不可访问',404)
    return row


def card(db,row,user,*,detail=False,submitted=False):
    frozen=row.submitted if submitted else row.approved
    value=frozen['value'];author=db.get(User,row.owner_id);profile=account(db,author)
    result={'id':row.id,'title':value['title'],'summary':value['summary'],'genre':value['genre'],'tags':value['tags'],
        'coverTheme':value['coverTheme'],'hasCover':bool(value['coverDataUrl']),'authorId':author.id,
        'authorName':profile.display_name or author.username,'version':frozen['authorVersion'],
        'favorite':bool(db.scalar(select(PublicFavorite.id).where(PublicFavorite.owner_id==user.id,PublicFavorite.publication_id==row.id))),
        'favorites':db.scalar(select(func.count()).select_from(PublicFavorite).where(PublicFavorite.publication_id==row.id)),
        'updatedAt':row.updated_at.isoformat()}
    if detail:
        definition=frozen['definition']
        result.update(introduction=value['introduction'],attribution=frozen['attribution'],openings=deepcopy(value['openings']),
            formTitle=value.get('formTitle','你的开局信息'),playerFields=deepcopy(value['playerFields']),characters=[{'id':e['id'],'name':e['name'],'description':e['description']}
                for e in definition['entities'] if e['type']=='character'],isOwner=row.owner_id==user.id)
        for character in result['characters']:
            if character['id'] in frozen.get('characterArtworks',{}):
                base='/api/reader/v1/authoring/publications/'+row.id+'/characters/'+character['id']
                character.update(avatarUrl=base+'/avatar',imageUrl=base+'/image')
    return result


@router.get('/worlds/{world_id}')
def read(world_id:str,user=Depends(current_user),db=Depends(get_db)):
    return read_draft(db,owned(db,World,world_id,user.id))


@router.put('/worlds/{world_id}')
def save(world_id:str,data:SaveAuthor,request:Request,user=Depends(current_user),db=Depends(get_db)):
    world=owned(db,World,world_id,user.id,lock=True)
    return mutate(db,user,request,'save:'+world.id,data.model_dump(),lambda:save_draft(db,world,data))


from .trials import TrialInput


@router.get('/worlds/{world_id}/test-model')
def trial_model(world_id:str,profileId:str|None=None,user=Depends(current_user),db=Depends(get_db)):
    owned(db,World,world_id,user.id)
    from .trials import model_estimate
    from ..play_controls.service import presentation
    return {**model_estimate(db,user.id,profileId),'models':presentation(db,user.id,'line','0'*32)['models']}


@router.get('/worlds/{world_id}/trials')
def list_trials(world_id:str,user=Depends(current_user),db=Depends(get_db)):
    owned(db,World,world_id,user.id)
    from .trials import present
    rows=db.scalars(select(World).where(World.owner_id==user.id,World.status!='deleted',
        World.source['authorTrial']['originWorldId'].astext==world_id).order_by(World.created_at.desc()).limit(30))
    return {'items':[present(db,w) for w in rows]}


@router.post('/worlds/{world_id}/trials',status_code=202)
def start_trial(world_id:str,data:TrialInput,request:Request,user=Depends(current_user),db=Depends(get_db)):
    world=owned(db,World,world_id,user.id,lock=True)
    from .trials import create
    return mutate(db,user,request,'trial:'+world.id,data.model_dump(),lambda:create(db,user.id,world,data))


@router.post('/worlds/{world_id}/submit')
def submit(world_id:str,data:SubmitPublication,request:Request,user=Depends(current_user),db=Depends(get_db)):
    world=owned(db,World,world_id,user.id,lock=True)
    def action():
        if world.source.get('authorTrial'):raise Problem('AUTHOR_TRIAL_PRIVATE','试玩不能直接公开，请返回原作品发布',409)
        if not data.rightsConfirmed or not data.attribution.strip():raise Problem('PUBLICATION_RIGHTS_REQUIRED','请确认拥有公开此作品的权利并填写来源说明',422)
        if world.status!='active':raise Problem('WORLD_NOT_READY','请先创建可玩的世界，再提交公开',409)
        draft=db.get(AuthorDraft,world.id)
        if not draft or draft.version!=data.authorVersion:raise Problem('VERSION_CONFLICT','作者配置已变化，请先保存并重新提交',409)
        row=db.scalar(select(Publication).where(Publication.world_id==world.id).with_for_update())
        if (row.version if row else 0)!=data.expectedVersion:raise Problem('VERSION_CONFLICT','发布状态已经更新，请重新载入',409)
        frozen={'value':deepcopy(draft.value),'definition':compile_definition(world,draft.value),
            'authorVersion':draft.version,'attribution':data.attribution.strip()}
        from .artworks import freeze
        frozen['characterArtworks']=freeze(db,world,frozen['definition'])
        if not row:row=Publication(id=uid(),owner_id=user.id,world_id=world.id,submitted=frozen,state='pending',approved={},note='',version=1);db.add(row)
        else:row.submitted=frozen;row.state='pending';row.note='';row.version+=1;row.updated_at=now()
        db.flush();return publication_owner(row)
    return mutate(db,user,request,'submit:'+world.id,data.model_dump(),action)


@router.post('/publications/{publication_id}/withdraw')
def withdraw(publication_id:str,data:VersionInput,request:Request,user=Depends(current_user),db=Depends(get_db)):
    row=owned(db,Publication,publication_id,user.id,lock=True)
    def action():
        if row.version!=data.expectedVersion:raise Problem('VERSION_CONFLICT','发布状态已经更新，请重新载入',409)
        row.live=False;row.state='withdrawn';row.version+=1;row.updated_at=now();return publication_owner(row)
    return mutate(db,user,request,'withdraw:'+row.id,data.model_dump(),action)


@router.get('/publications/{publication_id}')
def detail(publication_id:str,user=Depends(current_user),db=Depends(get_db)):
    return card(db,live_publication(db,publication_id),user,detail=True)


@router.get('/publications/{publication_id}/cover')
def cover(publication_id:str,user=Depends(current_user),db=Depends(get_db)):
    # A checked data URL avoids granting a private object URL or remote access.
    row=live_publication(db,publication_id)
    return {'dataUrl':row.approved['value']['coverDataUrl']}


@router.put('/publications/{publication_id}/favorite')
def favorite(publication_id:str,data:FavoriteInput,user=Depends(current_user),db=Depends(get_db)):
    row=live_publication(db,publication_id,lock=True)
    item=db.scalar(select(PublicFavorite).where(PublicFavorite.owner_id==user.id,PublicFavorite.publication_id==row.id))
    if data.favorite and not item:db.add(PublicFavorite(owner_id=user.id,publication_id=row.id))
    elif not data.favorite and item:db.delete(item)
    db.commit();return {'favorite':data.favorite}


@router.get('/publications/{publication_id}/characters/{entity_id}/{variant}')
def character_image(publication_id:str,entity_id:str,variant:str,user=Depends(current_user),db=Depends(get_db)):
    from ..scenes.models import CharacterArtwork
    row=live_publication(db,publication_id)
    identity=row.approved.get('characterArtworks',{}).get(entity_id)
    art=db.get(CharacterArtwork,identity) if identity else None
    if variant not in ('avatar','image') or not art or art.owner_id!=row.owner_id or art.world_id!=row.world_id or art.entity_id!=entity_id:
        raise Problem('CHARACTER_IMAGE_UNAVAILABLE','角色形象暂不可用',404)
    return Response(content=art.avatar_image if variant=='avatar' else art.full_image,media_type='image/webp',headers={'Cache-Control':'private, no-store'})


@router.post('/publications/{publication_id}/report')
def report(publication_id:str,data:ReportInput,user=Depends(current_user),db=Depends(get_db)):
    row=live_publication(db,publication_id,lock=True)
    if len(data.reason.strip())<5:raise Problem('REPORT_REASON_REQUIRED','请写明举报原因',422)
    item=db.scalar(select(PublicReport).where(PublicReport.owner_id==user.id,PublicReport.publication_id==row.id))
    if not item:db.add(PublicReport(owner_id=user.id,publication_id=row.id,reason=data.reason.strip()))
    else:item.reason=data.reason.strip();item.state='open'
    db.commit();return {'reported':True}


@router.post('/publications/{publication_id}/copy',status_code=201)
def copy(publication_id:str,request:Request,user=Depends(current_user),db=Depends(get_db)):
    row=live_publication(db,publication_id,lock=True)
    def action():
        frozen=row.approved;definition=deepcopy(frozen['definition'])
        for entry in [*definition['entities'],*definition.get('lore',[])]:
            entry['source']='user';entry['evidence']=[];entry.pop('sourceCharacterId',None)
        author_value=deepcopy(frozen['value'])
        for entry in [*author_value['definition']['entities'],*author_value['definition'].get('lore',[])]:
            entry['source']='user';entry['evidence']=[]
            if 'sourceCharacterId' in entry:entry['sourceCharacterId']=None
        world=World(id=uid(),owner_id=user.id,name=frozen['value']['title'],source_title=frozen['value']['title'],definition=definition,status='active',
            source={'kind':'standalone','schemaVersion':2,'title':frozen['value']['title'],'summary':frozen['value']['summary'],
                'publicationId':row.id,'publicationVersion':frozen['authorVersion'],'authorId':row.owner_id,
                'blocks':[],'chapters':[],'referenceBlocks':[],'fullBookReference':False,'anchor':None})
        db.add(world);db.flush()
        # A copied work remains editable without collapsing its multiple starts
        # or dropping the approved player/status fields on the first save.
        db.add(AuthorDraft(world_id=world.id,owner_id=user.id,version=1,value=author_value))
        from .artworks import clone
        clone(db,db.get(World,row.world_id),world,frozen.get('characterArtworks',{}))
        db.flush();return {'worldId':world.id,'publicationVersion':frozen['authorVersion']}
    return mutate(db,user,request,'copy:'+row.id,{},action)


@router.get('/moderation')
def moderation(offset:int=Query(default=0,ge=0),limit:int=Query(default=20,ge=1,le=50),user=Depends(current_user),db=Depends(get_db)):
    require_admin(db,user)
    query=select(Publication).where((Publication.state=='pending')|Publication.id.in_(select(PublicReport.publication_id).where(PublicReport.state=='open')))
    total=db.scalar(select(func.count()).select_from(query.subquery()))
    rows=db.scalars(query.order_by(Publication.updated_at,Publication.id).offset(offset).limit(limit))
    return {'total':total,'items':[{'publication':publication_owner(r),'card':card(db,r,user,submitted=r.state=='pending'),
        'snapshot':r.submitted if r.state=='pending' else r.approved,
        'reports':[{'id':v.id,'reason':v.reason} for v in db.scalars(select(PublicReport).where(PublicReport.publication_id==r.id,PublicReport.state=='open'))]} for r in rows]}


@router.post('/moderation/{publication_id}')
def review(publication_id:str,data:ReviewPublication,request:Request,user=Depends(current_user),db=Depends(get_db)):
    require_admin(db,user)
    row=db.scalar(select(Publication).where(Publication.id==publication_id).with_for_update())
    if not row:raise Problem('RESOURCE_NOT_FOUND','作品不存在',404)
    def action():
        if len(data.note.strip())<3:raise Problem('REVIEW_NOTE_REQUIRED','请填写明确的审核说明',422)
        if row.version!=data.expectedVersion:raise Problem('VERSION_CONFLICT','作品已更新，请重新审核',409)
        if data.decision in ('approve','reject') and row.state!='pending':raise Problem('REVIEW_STATE_INVALID','当前没有待审核版本',409)
        if data.decision=='approve':
            if not db.get(User,row.owner_id) or db.get(User,row.owner_id).status!='active' or not db.get(World,row.world_id) or db.get(World,row.world_id).status!='active':raise Problem('PUBLICATION_UNAVAILABLE','作者或原作品已不可用',409)
            row.approved=deepcopy(row.submitted);row.live=True;row.state='published'
        elif data.decision=='reject':row.state='rejected'
        else:row.live=False;row.state='removed'
        row.note=data.note.strip();row.reviewer_id=user.id;row.version+=1;row.updated_at=now()
        from ..experience.models import notify
        notify(db,row.owner_id,'作品审核状态已更新',row.note,'/worlds/'+row.world_id+'/author?tab=publish')
        for report in db.scalars(select(PublicReport).where(PublicReport.publication_id==row.id,PublicReport.state=='open')):
            notify(db,report.owner_id,'作品举报已处理',data.note.strip(),'/explore')
        for item in db.scalars(select(PublicReport).where(PublicReport.publication_id==row.id,PublicReport.state=='open')):item.state='resolved'
        from ..foundation.core import audit
        audit(db,user.id,'publication.'+data.decision,row.id,'publication-review:'+user.id+':'+request.headers.get('idempotency-key',''),{'version':row.version,'note':row.note})
        return publication_owner(row)
    return mutate(db,user,request,'review:'+row.id,data.model_dump(),action)
