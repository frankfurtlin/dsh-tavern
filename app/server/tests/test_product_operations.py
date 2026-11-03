"""Operational regressions on dedicated PostgreSQL; no external services."""
from io import BytesIO
from zipfile import ZipFile
import pytest
from PIL import Image
from sqlalchemy import select, text
from app.db import SessionLocal
from app.foundation.models import Product, KnowledgeDoc, KnowledgeChunk, StoredObject, Audit
from app.foundation.knowledge import extract, search_documents
from app.foundation.payments import confirm
from app.errors import Problem
from test_product_foundation import operator, new_user, add_order, confirmation, ROOT


@pytest.mark.parametrize('path', ['/admin/catalog', '/admin/orders'])
def test_operations_lists_require_admin(client, reader, path):
    assert client.get(ROOT + path).status_code == 403


def test_admin_catalog_includes_inactive_and_has_real_pagination(client, operator):
    with SessionLocal() as db:
        db.add_all([Product(sku='product_' + str(i), title='商品' + str(i), kind='credits',
                           amount_minor=100, currency='CNY', credits=20, active=i != 0) for i in range(53)])
        db.commit()
    assert len(client.get(ROOT + '/catalog').json()['items']) == 52
    first = client.get(ROOT + '/admin/catalog').json()
    second = client.get(ROOT + '/admin/catalog?offset=50').json()
    assert first['total'] == second['total'] == 53
    assert len(first['items']) == 50 and len(second['items']) == 3
    rows = first['items'] + second['items']
    assert len({r['id'] for r in rows}) == 53 and sum(not r['active'] for r in rows) == 1
    assert client.get(ROOT + '/admin/catalog?offset=-1').status_code == 422


def test_order_query_reports_only_verified_receipts_without_cashier_tokens(client, operator):
    target = new_user(client, 'order_owner')
    with SessionLocal() as db:
        paid = add_order(db, target)
        paid.checkout_url = 'https://pay.fixture.invalid?token=PRIVATE-CASHIER-SENTINEL'
        confirm(db, paid, **confirmation(paid))
        review = add_order(db, operator)
        review.state = 'review'
        db.commit()
        paid_id = paid.id
    result = client.get(ROOT + '/admin/orders?query=order_owner').json()
    assert result['total'] == 1
    row = result['items'][0]
    assert row['id'] == paid_id and row['state'] == 'paid' and row['verifiedPayments'] == 1
    assert row['checkoutUrl'] is None and 'PRIVATE-CASHIER-SENTINEL' not in str(result)
    assert client.get(ROOT + '/admin/orders?state=review').json()['total'] == 1
    assert client.get(ROOT + '/admin/orders?query=' + paid_id).json()['total'] == 1
    assert client.get(ROOT + '/admin/orders?query=%25').json()['total'] == 0
    with SessionLocal() as db:
        assert db.scalar(select(Audit).where(Audit.action == 'order.update')) is None


def test_epub_encoded_relative_spine_and_fragment_are_imported_in_order():
    output = BytesIO()
    with ZipFile(output, 'w') as z:
        z.writestr('META-INF/container.xml', '<container><rootfiles><rootfile full-path="OPS/package/book.opf"/></rootfiles></container>')
        z.writestr('OPS/package/book.opf', '<package xmlns="http://www.idpf.org/2007/opf"><manifest>'
                   '<item id="a" href="../%E7%AC%AC%E4%B8%80%20%E7%AB%A0.xhtml#start"/>'
                   '</manifest><spine><itemref idref="a"/></spine></package>')
        z.writestr('OPS/第一 章.xhtml', '<html><body><p>蓝宝石的出处</p><script>隐藏脚本</script></body></html>')
    result = extract('test.epub', output.getvalue())
    assert '蓝宝石的出处' in result and '隐藏脚本' not in result


@pytest.mark.parametrize('href', ['../../../outside.xhtml', 'https://other.invalid/chapter.xhtml'])
def test_epub_outside_package_is_a_parse_error(href):
    output = BytesIO()
    with ZipFile(output, 'w') as z:
        z.writestr('META-INF/container.xml', '<container><rootfile full-path="book.opf"/></container>')
        z.writestr('book.opf', '<package xmlns="http://www.idpf.org/2007/opf"><manifest><item id="a" href="' +
                   href + '"/></manifest><spine><itemref idref="a"/></spine></package>')
    with pytest.raises(Problem) as error:
        extract('test.epub', output.getvalue())
    assert error.value.code == 'KNOWLEDGE_PARSE_FAILED'


def test_chinese_rank_uses_all_candidates_and_remains_private(client, reader):
    owner = reader['user']['id']
    stranger = new_user(client, 'private_documents')
    with SessionLocal() as db:
        obj = StoredObject(owner_id=owner, name='案情.txt', object_key=owner + '/fixture.txt', driver='local',
                           content_type='text/plain', size=10, checksum='a' * 64, purpose='knowledge')
        db.add(obj); db.flush()
        doc = KnowledgeDoc(owner_id=owner, object_id=obj.id, title='案情', source_version='a' * 64)
        db.add(doc); db.flush()
        db.add_all([KnowledgeChunk(doc_id=doc.id, ordinal=i, text='蓝宝石线索') for i in range(510)])
        strongest = KnowledgeChunk(doc_id=doc.id, ordinal=511, text='蓝宝石被盗案：蓝宝石从窗边失窃。' * 8)
        db.add(strongest); db.flush(); best = strongest.id
        db.commit()
        hits = search_documents(db, owner, '蓝宝石被盗案')
        assert hits[0]['blockId'] == best
        assert search_documents(db, stranger, '蓝宝石') == []
        assert search_documents(db, owner, '蓝宝石失窃')


def test_deleting_current_avatar_clears_profile(client, reader):
    image = BytesIO(); Image.new('RGB', (8, 8), 'red').save(image, 'PNG')
    upload = client.post(ROOT + '/objects', files={'file': ('avatar.png', image.getvalue(), 'image/png')}, data={'purpose': 'avatar'})
    assert upload.status_code == 201, upload.text
    identity = upload.json()['id']
    assert client.put(ROOT + '/account', json={'avatarId': identity}).status_code == 200
    assert client.delete(ROOT + '/objects/' + identity).status_code == 200
    assert client.get(ROOT + '/account').json()['avatarId'] is None


def test_ready_detects_any_missing_product_table(client):
    with SessionLocal() as db:
        db.execute(text('ALTER TABLE product_knowledge_chunks RENAME TO product_knowledge_chunks_missing'))
        db.commit()
    try:
        response = client.get('/health/ready')
        assert response.status_code == 503 and response.json()['code'] == 'MIGRATION_REQUIRED'
    finally:
        with SessionLocal() as db:
            db.execute(text('ALTER TABLE product_knowledge_chunks_missing RENAME TO product_knowledge_chunks'))
            db.commit()
    assert client.get('/health/ready').status_code == 200
