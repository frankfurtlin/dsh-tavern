"""Dedicated PostgreSQL tests. Payment fixtures never contact a merchant."""
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from io import BytesIO
import json
from types import SimpleNamespace
from unittest.mock import patch
from zipfile import ZipFile
import pytest
from sqlalchemy import select, func, text
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from app.config import settings
from app.db import SessionLocal
from app.errors import Problem
from app.models import User, Task, UsageRecord, now, uid
from app.foundation.models import Account, Config, CreditLot, Ledger, Hold, Membership, Event, Order, Product, PaymentReceipt, StoredObject, KnowledgeDoc
from app.foundation.wallet import grant, hold, settle, balances, reserve_usage, settle_usage_credits
from app.foundation.payments import new_order, confirm, rsa_sign, rsa_verify, i6ls_notification, waffo_event
from app.foundation.events import checkpoint_facts

ROOT = '/api/reader/v1/product'


@pytest.fixture
def operator(reader):
    owner = reader['user']['id']
    with SessionLocal() as db:
        db.add(Account(owner_id=owner, role='ADMIN'))
        db.commit()
    return owner


def new_user(client, name='product_two'):
    result = client.post('/api/reader/v1/auth/register', json={'username':name,'password':'product-password-123'})
    assert result.status_code == 201, result.text
    return result.json()['id']


def rsa_pair():
    private = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    return (private.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()).decode(),
            private.public_key().public_bytes(serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo).decode())


@pytest.fixture
def merchant(monkeypatch):
    private, public = rsa_pair()
    for key, value in {'product_payment_provider':'i6ls','product_payment_environment':'test','product_i6ls_merchant':'fixture-merchant',
        'product_i6ls_base_url':'https://pay.fixture.invalid','product_i6ls_private_key':private,'product_i6ls_public_key':public,
        'product_payment_allowed_hosts':'pay.fixture.invalid','product_public_origin':'https://reader.fixture.invalid'}.items():
        monkeypatch.setattr(settings, key, value)
    return private, public


def add_order(db, owner, *, currency='CNY', provider='i6ls', member=False):
    product = Product(sku=uid(), title='测试商品', kind='member' if member else 'credits', amount_minor=990,
                      currency=currency, credits=100, member_days=30 if member else 0, daily_credits=10 if member else 0)
    db.add(product);db.flush()
    row = Order(owner_id=owner, product_id=product.id, request_key=uid(), provider=provider, environment='test', merchant='fixture-merchant',
        snapshot={'sku':product.sku,'title':product.title,'kind':product.kind,'amountMinor':990,'currency':currency,
                  'credits':100,'memberDays':product.member_days,'dailyCredits':product.daily_credits}, expires_at=now()+timedelta(minutes=45))
    db.add(row);db.flush()
    return row


def confirmation(row, **kw):
    return dict(provider=row.provider, environment='test', merchant='fixture-merchant', amount=990,
        currency=row.snapshot['currency'], payment_id='fixture-payment', event_id='fixture-event', body_hash='a'*64, **kw)


@pytest.mark.parametrize('path',['/admin/users','/admin/overview','/admin/policy','/admin/tasks','/admin/invites','/admin/audit'])
def test_ordinary_user_cannot_read_operations(client, reader, path):
    result=client.get(ROOT+path)
    assert result.status_code==403 and result.json()['code']=='ADMIN_REQUIRED'


def test_account_defaults_and_preferences_cannot_escalate(client, reader):
    profile=client.get(ROOT+'/account').json()
    assert profile['role']=='USER' and profile['language']=='zh-CN'
    assert client.put(ROOT+'/account',json={'role':'ADMIN'}).status_code==422
    assert client.put(ROOT+'/account',json={'displayName':'我的笔名','language':'en'}).status_code==200
    assert client.get(ROOT+'/account').json()['displayName']=='我的笔名'


def test_last_admin_cannot_be_disabled_or_demoted(client, operator):
    for body in ({'role':'USER','status':'active'},{'role':'ADMIN','status':'disabled'}):
        result=client.put(ROOT+'/admin/users/'+operator,json={**body,'requestKey':'last-admin-case'})
        assert result.status_code==409 and result.json()['code']=='LAST_ADMIN_REQUIRED'


def test_disable_revokes_devices_and_enable_supports_legacy_accounts(client, operator):
    owner=new_user(client)
    result=client.put(ROOT+'/admin/users/'+owner,json={'status':'disabled','role':'USER','memo':'内测暂停','requestKey':'disable-test-user'})
    assert result.status_code==200, result.text
    assert client.post('/api/reader/v1/auth/login',json={'username':'product_two','password':'product-password-123'}).status_code==401
    result=client.put(ROOT+'/admin/users/'+owner,json={'status':'active','role':'USER','requestKey':'enable-test-user'})
    assert result.status_code==200, result.text


def test_admin_grants_atomic_idempotent_audited_and_not_cash(client, operator):
    body={'requestKey':'gift-000001','credits':120,'memberDays':7,'dailyCredits':10,'reason':'首轮内测体验'}
    for _ in range(2):
        assert client.post(ROOT+'/admin/users/'+operator+'/grant',json=body).status_code==200
    wallet=client.get(ROOT+'/wallet').json()
    assert wallet['available']==120 and len(wallet['ledger'])==1 and len(wallet['memberships'])==1
    assert client.get(ROOT+'/admin/overview').json()['revenue']==[]
    conflict=client.post(ROOT+'/admin/users/'+operator+'/grant',json={**body,'credits':121})
    assert conflict.status_code==409
    assert client.get(ROOT+'/wallet').json()['available']==120


def test_wallet_holds_partial_settlement_and_replay(reader):
    owner=reader['user']['id']
    with SessionLocal() as db:
        grant(db,owner,100,'fixture-grant')
        first=hold(db,owner,'business-call','task','generation',40,{'version':1})
        assert hold(db,owner,'business-call','task','generation',40,{'version':1}).id==first.id
        assert balances(db,owner)['available']==60
        settle(db,owner,'business-call',25);settle(db,owner,'business-call',25)
        assert balances(db,owner)=={'buckets':{'DAILY':{'available':0,'frozen':0},'TEMPORARY':{'available':0,'frozen':0},'PERMANENT':{'available':75,'frozen':0}},'available':75,'frozen':0}
        with pytest.raises(Problem,match='另一金额'):settle(db,owner,'business-call',30)
        db.commit()
    with SessionLocal() as db:
        assert db.scalar(select(func.count()).select_from(Ledger))==3


def test_expired_unknown_hold_never_releases_credits(reader):
    owner=reader['user']['id']
    with SessionLocal() as db:
        lot=grant(db,owner,100,'expiring',bucket='TEMPORARY',expires=now()+timedelta(days=1))
        hold(db,owner,'unknown-call','task','generation',30,{})
        settle(db,owner,'unknown-call',0,unknown=True)
        lot.expires_at=now()-timedelta(days=1);db.flush()
        assert balances(db,owner)['frozen']==30 and balances(db,owner)['available']==0
        settle(db,owner,'unknown-call',20);db.commit()
        assert lot.remaining==80 and lot.frozen==0


def test_concurrent_duplicate_hold_and_grant_are_exactly_once(reader):
    owner=reader['user']['id']
    def action(_):
        with SessionLocal() as db:
            grant(db,owner,100,'concurrent-grant')
            result=hold(db,owner,'concurrent-call','task','generation',30,{})
            db.commit();return result.id
    with ThreadPoolExecutor(max_workers=4) as pool:
        ids=list(pool.map(action,range(8)))
    assert len(set(ids))==1
    with SessionLocal() as db:
        assert balances(db,owner)['available']==70 and balances(db,owner)['frozen']==30
        assert db.scalar(select(func.count()).select_from(CreditLot))==1
        assert db.scalar(select(func.count()).select_from(Hold))==1


def test_insufficient_hold_rolls_back_and_disabled_billing_preserves_existing_calls(reader):
    owner=reader['user']['id']
    with SessionLocal() as db:
        grant(db,owner,20,'credit-20');db.commit()
        with pytest.raises(Problem):hold(db,owner,'too-much','task','generation',21,{})
        db.rollback()
        assert balances(db,owner)['available']==20
        usage=UsageRecord(owner_id=owner,task_id=uid(),provider_call_id=uid(),profile_id=uid(),consent_id=uid(),operation='generation',reserved_input=20,reserved_output=20)
        reserve_usage(db,usage)
        assert db.scalar(select(func.count()).select_from(Hold))==0


def test_actual_usage_bridge_freezes_settles_once_and_refunds_unsent(reader):
    owner=reader['user']['id']
    with SessionLocal() as db:
        grant(db,owner,100,'credit-100')
        db.add(Config(key='billing',value={'enabled':True,'version':3,'prices':{'generation':15}}));db.flush()
        usage=UsageRecord(owner_id=owner,task_id=uid(),provider_call_id=uid(),profile_id=uid(),consent_id=uid(),operation='generation',reserved_input=20,reserved_output=20,status='intent')
        reserve_usage(db,usage);db.flush()
        assert balances(db,owner)['frozen']==15
        usage.status='unknown';settle_usage_credits(db,usage)
        assert db.scalar(select(Hold)).state=='unknown'
        usage.status='completed';settle_usage_credits(db,usage);settle_usage_credits(db,usage)
        assert balances(db,owner)['available']==85
        second=UsageRecord(owner_id=owner,task_id=uid(),provider_call_id=uid(),profile_id=uid(),consent_id=uid(),operation='generation',reserved_input=20,reserved_output=20,status='not_sent')
        reserve_usage(db,second);settle_usage_credits(db,second)
        assert balances(db,owner)['available']==85 and balances(db,owner)['frozen']==0
        db.commit()


def test_card_redemption_replays_but_other_account_cannot_claim(client, operator):
    card=client.post(ROOT+'/admin/cards',json={'requestKey':'card-fixture-01','credits':50,'memberDays':7}).json()
    assert client.post(ROOT+'/membership/redeem',json={'code':card['code']}).status_code==200
    assert client.post(ROOT+'/membership/redeem',json={'code':card['code']}).json()['alreadyRedeemed']
    other=new_user(client)
    login=client.post('/api/reader/v1/auth/login',json={'username':'product_two','password':'product-password-123','clientType':'android'}).json()
    client.headers['Authorization']='Bearer '+login['accessToken']
    assert client.post(ROOT+'/membership/redeem',json={'code':card['code']}).status_code==409


def test_invite_gate_quota_expiry_and_reward_are_atomic(client, operator):
    invite=client.post(ROOT+'/admin/invites',json={'requestKey':'invite-fixture-01','label':'一人测试','maxUses':1,'reward':100}).json()
    assert client.put(ROOT+'/admin/policy',json={'requestKey':'policy-gate-on','inviteRequired':True}).status_code==200
    assert client.post('/api/reader/v1/auth/register',json={'username':'no_invite','password':'product-password-123'}).status_code==403
    first=client.post('/api/reader/v1/auth/register',json={'username':'invited_one','password':'product-password-123','inviteCode':invite['code']})
    assert first.status_code==201,first.text
    assert client.post('/api/reader/v1/auth/register',json={'username':'invited_two','password':'product-password-123','inviteCode':invite['code']}).status_code==403
    with SessionLocal() as db:
        assert balances(db,first.json()['id'])['available']==100
        assert db.scalar(select(func.count()).select_from(User))==2


def test_daily_claim_replay_cannot_replenish_spent_credits(client, operator):
    body={'requestKey':'daily-member-fixture','memberDays':30,'dailyCredits':20,'reason':'测试每日额度'}
    assert client.post(ROOT+'/admin/users/'+operator+'/grant',json=body).status_code==200
    assert client.post(ROOT+'/growth/daily-claim').json()['amount']==20
    with SessionLocal() as db:
        hold(db,operator,'spent-daily','task','generation',20,{})
        settle(db,operator,'spent-daily',20);db.commit()
    assert client.post(ROOT+'/growth/daily-claim').json()['alreadyClaimed']
    assert client.get(ROOT+'/wallet').json()['available']==0


def test_policy_and_catalog_replay_does_not_undo_later_updates(client, operator):
    old={'requestKey':'policy-original','inviteRequired':False,'signinCredits':5}
    new={'requestKey':'policy-updated','inviteRequired':True,'signinCredits':10}
    assert client.put(ROOT+'/admin/policy',json=old).status_code==200
    assert client.put(ROOT+'/admin/policy',json=new).status_code==200
    assert client.put(ROOT+'/admin/policy',json=old).json()['replayed']
    policy=client.get(ROOT+'/admin/policy').json()
    assert policy['beta']['inviteRequired'] and policy['growth']['signinCredits']==10 and policy['billing']['version']==2


def test_payments_disabled_without_private_merchant_configuration(client, operator):
    item=client.put(ROOT+'/admin/catalog',json={'requestKey':'product-fixture-01','sku':'beta-credits','title':'积分包','kind':'credits','amountMinor':990,'currency':'CNY','credits':100}).json()
    assert client.post(ROOT+'/orders',json={'requestKey':'order-fixture-01','productId':item['id']}).status_code==503
    assert client.post(ROOT+'/payments/waffo/webhook',content=b'{}').status_code==503


def test_verified_payment_snapshots_idempotency_and_revenue(client, operator, merchant):
    with SessionLocal() as db:
        row=add_order(db,operator);identity=row.id
        fields=confirmation(row)
        confirm(db,row,**fields);confirm(db,row,**fields);db.commit()
        assert balances(db,operator)['available']==100
        db.get(Product,row.product_id).credits=999;db.commit()
        assert db.get(Order,identity).snapshot['credits']==100
        assert db.scalar(select(func.count()).select_from(PaymentReceipt))==1
    result=client.get(ROOT+'/admin/overview').json()
    assert result['revenue']==[{'currency':'CNY','environment':'test','amount':990,'payments':1}]


@pytest.mark.parametrize('field,value',[('amount',991),('currency','USD'),('merchant','wrong'),('environment','prod'),('provider','waffo')])
def test_mismatched_signed_receipt_never_grants(reader, field, value):
    with SessionLocal() as db:
        row=add_order(db,reader['user']['id']);db.commit()
        fields=confirmation(row);fields[field]=value
        with pytest.raises(Problem):confirm(db,row,**fields)
        db.rollback()
        assert db.scalar(select(func.count()).select_from(PaymentReceipt))==0
        assert balances(db,row.owner_id)['available']==0 and db.get(Order,row.id).state=='pending'


def test_real_rsa_notify_verifies_exact_merchant_and_amount(client, operator, merchant):
    with SessionLocal() as db:
        row=add_order(db,operator);db.commit();identity=row.id
    fields={'pid':'fixture-merchant','out_trade_no':identity,'trade_no':'rsa-payment','trade_status':'TRADE_SUCCESS','money':'9.90','type':'alipay'}
    fields['sign']=rsa_sign(fields,merchant[0]);fields['sign_type']='RSA'
    assert rsa_verify(fields,merchant[1])
    for _ in range(2):
        assert client.post(ROOT+'/payments/i6ls/notify',data=fields).status_code==200
    assert client.get(ROOT+'/wallet').json()['available']==100
    fields['money']='99.90'
    assert client.post(ROOT+'/payments/i6ls/notify',data=fields).status_code==400
    assert client.get(ROOT+'/wallet').json()['available']==100


def test_payment_receipt_replay_conflict_and_transaction_rollback(reader):
    with SessionLocal() as db:
        row=add_order(db,reader['user']['id']);db.commit()
        with patch('app.foundation.payments.emit',side_effect=RuntimeError('fixture persistence fault')):
            with pytest.raises(RuntimeError):confirm(db,row,**confirmation(row))
        db.rollback()
        assert balances(db,row.owner_id)['available']==0
        confirm(db,row,**confirmation(row));db.commit()
        fields=confirmation(row);fields['body_hash']='b'*64
        with pytest.raises(Problem):confirm(db,row,**fields)


def test_waffo_currency_is_checked_against_order_and_grants_exact_period(reader):
    with SessionLocal() as db:
        row=add_order(db,reader['user']['id'],currency='EUR',provider='waffo',member=True)
        row.snapshot={**row.snapshot,'amountMinor':990};db.commit()
        start=now().replace(microsecond=0);end=start+timedelta(days=31)
        confirm(db,row,**confirmation(row,period=[start.isoformat(),end.isoformat()]));db.commit()
        member=db.scalar(select(Membership))
        assert member.starts_at==start and member.ends_at==end and balances(db,row.owner_id)['available']==100


def test_waffo_missing_period_cannot_commit_partial_credit_grant(reader):
    with SessionLocal() as db:
        row=add_order(db,reader['user']['id'],currency='USD',provider='waffo',member=True);db.commit()
        with pytest.raises(Problem):confirm(db,row,**confirmation(row))
        db.rollback()
        assert balances(db,row.owner_id)['available']==0 and db.scalar(select(func.count()).select_from(PaymentReceipt))==0


def test_waffo_refunds_go_to_review_and_prevent_new_entitlements(reader):
    with SessionLocal() as db:
        row=add_order(db,reader['user']['id'],currency='USD',provider='waffo');db.commit()
        event={'eventId':'refund-fixture','eventType':'refund.succeeded','mode':'test','storeId':'fixture-merchant',
               'data':{'orderMerchantExternalId':'WF_'+row.id}}
        waffo_event(db,event);db.commit()
        assert row.state=='review'
        with pytest.raises(Problem):confirm(db,row,**confirmation(row))
        assert balances(db,row.owner_id)['available']==0


def telemetry_event(owner):
    return {'eventId':'event-fixture-01','eventVersion':2,'appVersion':'fixture','ownerUserId':owner,'type':'page_view','name':'/product/wallet',
            'path':'/worlds/'+'a'*32+'/play?private=secret','metadata':{'prompt':'private novel','email':'private@example.com','version':3},
            'anonId':'anonymous-fixture','sessionId':'session-fixture','deviceType':'desktop','ts':1}


def test_telemetry_ids_retries_privacy_and_owner_isolation(client, operator):
    event=telemetry_event(operator)
    body={'eventVersion':2,'expectedUserId':operator,'events':[event]}
    for _ in range(2):
        assert client.post(ROOT+'/telemetry/track',json=body).json()['data']=={'persisted':True,'accepted':1}
    with SessionLocal() as db:
        row=db.scalar(select(Event).where(Event.event_key=='client:'+operator+':event-fixture-01'))
        assert row.path=='/worlds/:id/play' and row.data['metadata']=={'version':3}
        assert db.scalar(select(func.count()).select_from(Event).where(Event.event_key=='client:'+operator+':event-fixture-01'))==1
    assert client.post(ROOT+'/telemetry/track',json={**body,'expectedUserId':'b'*32}).status_code==409
    assert client.post(ROOT+'/telemetry/track',json={**body,'events':[{**event,'eventId':'forged-fact','name':'fact.aftermath.resolved'}]}).status_code==403


def test_d1_immature_is_unknown_and_gifts_are_not_revenue(client, operator):
    value=client.get(ROOT+'/admin/overview').json()
    assert value['retention'][0]['d1'] is None and value['retention'][0]['returned'] is None
    assert value['providerCost']==[] and value['revenue']==[]


@pytest.mark.parametrize('extension',['txt','md','docx','epub'])
def test_knowledge_upload_parses_all_formats_and_returns_versioned_citations(client, reader, extension):
    content='蓝宝石的交接记录，来自作者的私人创作资料。'.encode()
    if extension in ('docx','epub'):
        buffer=BytesIO()
        with ZipFile(buffer,'w') as archive:
            if extension=='docx':
                archive.writestr('word/document.xml','<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:p><w:r><w:t>蓝宝石的交接记录</w:t></w:r></w:p></w:document>')
            else:
                archive.writestr('META-INF/container.xml','<container><rootfile full-path="content/package.opf"/></container>')
                archive.writestr('content/package.opf','<package xmlns="urn:epub"><manifest><item id="one" href="one.xhtml"/></manifest><spine><itemref idref="one"/></spine></package>')
                archive.writestr('content/one.xhtml','<p>蓝宝石的交接记录</p><script>不该索引的脚本</script>')
        content=buffer.getvalue()
    result=client.post(ROOT+'/objects',files={'file':('facts.'+extension,content)},data={'purpose':'knowledge'})
    assert result.status_code==201,result.text
    hit=client.post(ROOT+'/knowledge/search',json={'query':'蓝宝石'}).json()['items'][0]
    assert hit['scope']=='author' and hit['sourceVersion'] and hit['blockId'] and '脚本' not in hit['text']
    assert client.get(ROOT+'/objects/'+result.json()['id']+'/content').content==content
    assert client.delete(ROOT+'/objects/'+result.json()['id']).status_code==200
    assert client.post(ROOT+'/knowledge/search',json={'query':'蓝宝石'}).json()['items']==[]


def test_duplicate_knowledge_upload_reuses_original_object(client, reader):
    body={'files':{'file':('same.md','蓝宝石'.encode())},'data':{'purpose':'knowledge'}}
    first=client.post(ROOT+'/objects',**body).json()
    second=client.post(ROOT+'/objects',**body).json()
    assert first['id']==second['id'] and first['documentId']==second['documentId']
    assert len(client.get(ROOT+'/objects').json()['items'])==1


def test_object_and_document_authorization_and_invalid_archives(client, operator):
    other=new_user(client)
    with SessionLocal() as db:
        from app.foundation.storage import upload
        obj=upload(db,other,'private.md',b'private facts');db.commit();identity=obj.id
    assert client.get(ROOT+'/objects/'+identity+'/content').status_code==404
    assert client.delete(ROOT+'/objects/'+identity).status_code==404
    assert client.post(ROOT+'/objects',files={'file':('bad.docx',b'not a zip')},data={'purpose':'knowledge'}).status_code==422
    assert client.post(ROOT+'/objects',files={'file':('fake.png',b'not an image')}).status_code==422
    assert client.post(ROOT+'/knowledge/search',json={'query':'private','scope':'read'}).status_code==422


def test_player_search_never_mounts_author_documents(client, reader, profile):
    from app.world.models import WorldCheckpoint
    from app.world.worker import process
    prepared=client.post('/api/world/v1/scripts/samples/blue-carbuncle/prepared',json={},headers={'Idempotency-Key':'sample-foundation'}).json()
    review=client.get('/api/world/v1/worlds/'+prepared['worldId']+'/script').json()
    client.post('/api/world/v1/worlds/'+prepared['worldId']+'/script/publish',json={'expectedVersion':review['worldVersion']},headers={'Idempotency-Key':'publish-foundation'})
    # No model needed: use the formal initial checkpoint from begin.
    response=client.post('/api/world/v1/worlds/'+prepared['worldId']+'/begin',json={'characterId':'watson'},headers={'Idempotency-Key':'begin-foundation'})
    assert response.status_code==202,response.text
    start=response.json()
    uploaded=client.post(ROOT+'/objects',files={'file':('private.md','作者秘密：隐藏凶手全名'.encode())},data={'purpose':'knowledge'})
    assert uploaded.status_code==201
    result=client.post(ROOT+'/knowledge/search',json={'query':'作者秘密','scope':'player','worldId':prepared['worldId'],'lineId':start['lineId']})
    assert result.status_code==200 and result.json()['items']==[],result.text
    with SessionLocal() as db:
        assert db.scalar(select(func.count()).select_from(Event).where(Event.name=='fact.sample.entered'))==1


@pytest.mark.parametrize('phase',['selected','resolving','resolved'])
def test_blue_endings_facts_use_committed_phase_and_deduplicate(reader, phase):
    from .test_blue_carbuncle_settlement import choose
    _,definition,state,_=choose('end_release')
    previous=json.loads(json.dumps(state));previous['plot']['completed']=[];previous['plot'].pop('resolution')
    state['plot']['resolution']['phase']=phase
    cp=SimpleNamespace(id=uid(),owner_id=reader['user']['id'],world_id=uid(),line_id=uid(),kind='turn',state=state,definition=definition)
    parent=SimpleNamespace(state=previous)
    with SessionLocal() as db:
        checkpoint_facts(db,cp,parent);checkpoint_facts(db,cp,parent);db.commit()
        assert db.scalar(select(func.count()).select_from(Event).where(Event.name=='fact.investigation.completed'))==1
        assert db.scalar(select(func.count()).select_from(Event).where(Event.name=='fact.aftermath.resolved'))==(1 if phase=='resolved' else 0)


def test_invite_quota_concurrent_signup_has_one_winner(client, operator):
    invite=client.post(ROOT+'/admin/invites',json={'requestKey':'concurrent-invite','label':'并发名额','maxUses':1,'reward':50}).json()
    def signup(index):
        return client.post('/api/reader/v1/auth/register',json={'username':'concurrent_'+str(index),'password':'product-password-123','inviteCode':invite['code']}).status_code
    with ThreadPoolExecutor(max_workers=2) as pool:codes=list(pool.map(signup,[1,2]))
    assert sorted(codes)==[201,403]
    with SessionLocal() as db:
        assert db.scalar(select(func.count()).select_from(CreditLot))==1


def test_report_connection_enforces_database_read_only(client, operator):
    def attempt_write(db):
        db.execute(text("INSERT INTO product_config(key,value) VALUES ('forbidden-report-write','{}')"))
    with patch('app.foundation.reporting.overview',side_effect=attempt_write):
        assert client.get(ROOT+'/admin/overview').status_code==503
    with SessionLocal() as db:assert db.get(Config,'forbidden-report-write') is None


def test_body_limit_is_enforced_before_multipart_or_webhook_parsing(client, reader):
    assert client.post(ROOT+'/objects',content=b'x',headers={'Content-Length':str(10*1024*1024)}).status_code==413
    assert client.post(ROOT+'/telemetry/track',content=b'x',headers={'Content-Length':'70000'}).status_code==413
    assert client.post(ROOT+'/payments/waffo/webhook',content=b'x',headers={'Content-Length':'300000'}).status_code==413


def test_image_billing_receipt_is_idempotent_and_unknown_stays_reserved(reader):
    from app.foundation.wallet import reserve_scene,settle_scene
    owner=reader['user']['id']
    row=SimpleNamespace(id=uid(),owner_id=owner)
    with SessionLocal() as db:
        grant(db,owner,100,'image-test-credit')
        db.add(Config(key='billing',value={'enabled':True,'prices':{'scene_image':10}}));db.flush()
        reserve_scene(db,row);reserve_scene(db,row)
        settle_scene(db,row);db.flush()
        assert balances(db,owner)['frozen']==10
        assert db.scalar(select(Hold)).state=='unknown'
        settle_scene(db,row,paid=True);settle_scene(db,row,paid=True);settle_scene(db,row)
        assert balances(db,owner)['available']==90 and balances(db,owner)['frozen']==0
        db.commit()


def test_multi_chapter_stage_inherits_base_price_without_repricing_old_hold(reader):
    owner=reader['user']['id']
    with SessionLocal() as db:
        grant(db,owner,100,'multi-chapter-credit')
        policy=Config(key='billing',value={'enabled':True,'version':1,'prices':{'generation':7}});db.add(policy);db.flush()
        usage=UsageRecord(owner_id=owner,task_id=uid(),provider_call_id=uid(),profile_id=uid(),consent_id=uid(),operation='generation:2',reserved_input=10,reserved_output=10,status='intent')
        reserve_usage(db,usage);db.flush()
        policy.value={'enabled':True,'version':2,'prices':{'generation':70}};db.flush()
        usage.status='completed';settle_usage_credits(db,usage)
        assert balances(db,owner)['available']==93


def test_waffo_receiver_passes_exact_raw_signed_bytes_and_commits_once(client, operator, monkeypatch):
    monkeypatch.setattr(settings,'product_payment_provider','waffo')
    monkeypatch.setattr(settings,'product_waffo_service_token','fixture-token-'+('x'*40))
    monkeypatch.setattr(settings,'product_public_origin','https://reader.fixture.invalid')
    with SessionLocal() as db:
        row=add_order(db,operator,provider='waffo',currency='USD');db.commit();identity=row.id
    event={'eventId':'waffo-once','eventType':'order.completed','mode':'test','storeId':'fixture-merchant',
        'data':{'orderMerchantExternalId':'WF_'+identity,'paymentId':'waffo-payment','chargedAmount':'9.90','currency':'USD'}}
    raw=b'{ "signed": "raw whitespace is preserved" }'
    with patch('app.foundation.api.waffo',return_value=event) as verifier:
        for _ in range(2):
            result=client.post(ROOT+'/payments/waffo/webhook',content=raw,headers={'X-Waffo-Signature':'fixture-signature'})
            assert result.status_code==200,result.text
        assert verifier.call_args.kwargs['raw']==raw
    assert client.get(ROOT+'/wallet').json()['available']==100
    assert client.post(ROOT+'/payments/waffo/webhook',content=raw).status_code==400


def test_paid_account_cleanup_removes_private_data_but_keeps_anonymous_numeric_receipt(client, operator):
    from app.worker import process
    with SessionLocal() as db:
        row=add_order(db,operator);confirm(db,row,**confirmation(row));db.commit()
    result=client.post('/api/reader/v1/auth/delete-account',json={'currentPassword':'reader-password-123','confirmation':'删除我的账号'})
    assert result.status_code==202,result.text
    process(result.json()['taskId'])
    with SessionLocal() as db:
        assert db.get(User,operator) is None
        receipt=db.scalar(select(PaymentReceipt));assert receipt.verified and receipt.order_id is None
        assert receipt.details['amountMinor']==990


def test_schema_downgrade_refuses_to_discard_financial_rows(client, operator):
    from alembic import command
    from alembic.config import Config as MigrationConfig
    client.post(ROOT+'/admin/users/'+operator+'/grant',json={'requestKey':'downgrade-protection','credits':10,'reason':'迁移保护'})
    with pytest.raises(RuntimeError,match='financial'):command.downgrade(MigrationConfig('alembic.ini'),'0014')
    with SessionLocal() as db:
        assert db.scalar(text('SELECT version_num FROM alembic_version'))=='0022'
        assert balances(db,operator)['available']==10
    assert client.get('/health/ready').status_code==200


def test_same_payment_cannot_grant_to_another_order_even_for_memberships(reader):
    owner=reader['user']['id']
    with SessionLocal() as db:
        first=add_order(db,owner,member=True);second=add_order(db,owner,member=True);db.commit()
        confirm(db,first,**confirmation(first));db.commit()
        fields=confirmation(second);fields['event_id']='different-event'
        with pytest.raises(Problem,match='另一订单'):confirm(db,second,**fields)
        db.rollback()
        assert db.scalar(select(func.count()).select_from(PaymentReceipt))==1
        assert db.scalar(select(func.count()).select_from(Membership))==1


def test_duplicate_payment_events_reconcile_without_extending_member_period(reader):
    with SessionLocal() as db:
        row=add_order(db,reader['user']['id'],provider='waffo',currency='EUR',member=True)
        start=now().replace(microsecond=0);end=start+timedelta(days=31)
        confirm(db,row,**confirmation(row,period=[start.isoformat(),end.isoformat()]));db.commit()
        fields=confirmation(row);fields['event_id']='merchant-recovery-event';fields['body_hash']='b'*64
        confirm(db,row,**fields);db.commit()
        assert db.scalar(select(func.count()).select_from(PaymentReceipt))==1
        assert db.scalar(select(Membership)).ends_at==end


def test_reading_search_validates_anchor_and_cannot_unlock_unread_text(client, reader, demo):
    from app.models import Revision, ContentBlock, Progress
    from app.domain import make_anchor, utf16_len
    with SessionLocal() as db:
        revision=db.get(Revision,demo['revisionId'])
        first=db.get(ContentBlock,revision.manifest[0]['blocks'][0])
        last=db.get(ContentBlock,revision.manifest[-1]['blocks'][-1])
        before=make_anchor(demo['bookId'],demo['branchId'],revision.id,first,0,0)
        observed=make_anchor(demo['bookId'],demo['branchId'],revision.id,first,0,utf16_len(first.text))
        future=make_anchor(demo['bookId'],demo['branchId'],revision.id,last,0,utf16_len(last.text))
        progress=db.scalar(select(Progress).where(Progress.owner_id==reader['user']['id'],Progress.branch_id==demo['branchId']))
        if not progress:
            progress=Progress(owner_id=reader['user']['id'],book_id=demo['bookId'],branch_id=demo['branchId'],revision_id=revision.id,
                anchor=observed,max_observed_anchor=observed,version=1)
            db.add(progress)
        else:progress.max_observed_anchor=observed
        db.commit()
    forged=client.post(ROOT+'/knowledge/search',json={'query':'雾港','scope':'read','anchor':{**before,'quoteHash':'0'*64}})
    assert forged.status_code==422 and forged.json()['code']=='ANCHOR_INVALID'
    result=client.post(ROOT+'/knowledge/search',json={'query':'雾港','scope':'read','anchor':future})
    assert result.status_code==422 and result.json()['code']=='AI_CONTEXT_NOT_READY'


def test_valid_beta_invite_allows_cohort_behind_shared_ip(client, operator):
    from app.models import RateLimit
    from app.domain import digest
    invite=client.post(ROOT+'/admin/invites',json={'requestKey':'shared-nat-invite','label':'同网络内测','maxUses':10}).json()
    with SessionLocal() as db:
        row=db.get(RateLimit,digest('register:testclient'))
        row.count=5;db.commit()
    result=client.post('/api/reader/v1/auth/register',json={'username':'same_network','password':'product-password-123','inviteCode':invite['code']})
    assert result.status_code==201,result.text


def test_operator_hold_reconcile_only_uses_verified_usage(client, operator):
    call=uid()
    with SessionLocal() as db:
        grant(db,operator,100,'reconcile-credit')
        row=hold(db,operator,'provider:'+call,uid(),'generation',10,{'credits':10})
        identity=row.id;db.commit()
    result=client.post(ROOT+'/admin/holds/'+identity+'/reconcile')
    assert result.status_code==200 and not result.json()['verified']
    assert client.get(ROOT+'/wallet').json()['frozen']==10
    with SessionLocal() as db:
        db.add(UsageRecord(owner_id=operator,task_id=uid(),provider_call_id=call,profile_id=uid(),consent_id=uid(),operation='generation',reserved_input=10,reserved_output=10,status='completed'))
        db.commit()
    for _ in range(2):assert client.post(ROOT+'/admin/holds/'+identity+'/reconcile').json()['verified']
    assert client.get(ROOT+'/wallet').json()['available']==90


def test_s3_adapter_preserves_private_owner_keys_and_closes_responses(client, reader, monkeypatch):
    store={};closed=[]
    class ObjectStore:
        def put_object(self,bucket,key,stream,size,content_type):store[bucket,key]=stream.read()
        def get_object(self,bucket,key):
            return SimpleNamespace(read=lambda:store[bucket,key],close=lambda:closed.append('close'),release_conn=lambda:closed.append('release'))
        def remove_object(self,bucket,key):store.pop((bucket,key),None)
    monkeypatch.setattr(settings,'product_storage_driver','s3')
    with patch('app.foundation.storage.s3_client',return_value=ObjectStore()):
        result=client.post(ROOT+'/objects',files={'file':('facts.md','蓝宝石'.encode())},data={'purpose':'knowledge'})
        assert result.status_code==201,result.text
        assert next(iter(store))[0]=='private-data' and next(iter(store))[1].startswith(reader['user']['id']+'/')
        assert client.get(ROOT+'/objects/'+result.json()['id']+'/content').content=='蓝宝石'.encode()
        assert closed==['close','release']
        assert client.delete(ROOT+'/objects/'+result.json()['id']).status_code==200 and not store
