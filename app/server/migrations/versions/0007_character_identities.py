"""Add stable identity aliases without rewriting historical narrative IDs/data."""
from datetime import datetime, timezone
import hashlib
import json
from uuid import uuid5, NAMESPACE_URL, uuid4, UUID
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0007"
down_revision = "0006"


def world_point(source, definition, entity):
    blocks={b['blockId']:b['text'] for b in source.get('blocks',[])}
    def point(person):
        if person.get('source')!='original':return None
        for evidence in person.get('evidence',[]):
            quote=evidence.get('quote','');body=blocks.get(evidence.get('blockId'),'');start=body.find(quote)
            if not quote or start<0 or body.find(quote,start+1)>=0:continue
            names=[person['name']] if person['name'] in quote else [a for a in person.get('aliases',[]) if a in quote]
            if len(names)!=1:continue
            offset=start+quote.find(names[0])
            return {'blockId':evidence['blockId'],'start':len(body[:offset].encode('utf-16-le'))//2,'originalLabel':names[0]}
    proof=point(entity)
    if proof and any(other['id']!=entity['id'] and other['type']=='character' and point(other)==proof
                     for other in definition.get('entities',[])):return None
    return proof


def world_reference(source, entity, proof):
    explicit=entity.get('sourceCharacterId')
    if not proof or entity.get('source')!='original':return None
    matches={ref['identityId'] for ref in source.get('characterRefs',[]) if
        (not explicit or ref['identityId']==explicit) and
        (entity['name']==ref['name'] or ref['name'] in entity.get('aliases',[])) and
        (proof in ref.get('proofs',[ref.get('proof')]) if explicit else ref.get('proof')==proof) and
        any(p['blockId']==e['blockId'] and ref['name'] in e['quote'] and
            (p['quote'] in e['quote'] or e['quote'] in p['quote']) and
            any(b['blockId']==e['blockId'] and e['quote'] in b['text'] for b in source.get('blocks',[]))
            for p in ref.get('evidence',[]) for e in entity.get('evidence',[]))}
    return next(iter(matches)) if len(matches)==1 else None


def reader_reconstruction(bind):
    """Same exact reconstruction for upgrade and lossless downgrade checks."""
    result = {}
    scopes = {(r['owner_id'], r['id']): r for r in bind.execute(sa.text(
        'SELECT b.id,b.owner_id,b.book_id,s.source_map FROM branches b '
        'JOIN sources s ON s.book_id=b.book_id AND s.owner_id=b.owner_id')).mappings()}
    sources = {(r['owner_id'], r['book_id']): r['source_map'] for r in scopes.values()}
    for (owner, branch), scope in scopes.items():
        mapping = scope['source_map']
        if mapping.get('kind') != 'detached_publication':
            continue
        for ref in mapping.get('characterAliasBindings', []):
            if ref.get('branchId') != branch or not isinstance(ref.get('entityId'), str) or not ref['entityId']:
                continue
            identifier = ref.get('identityId')
            try:
                if not isinstance(identifier, str) or UUID(hex=identifier).hex != identifier:
                    continue
            except ValueError:
                continue
            key = (owner, branch, ref['entityId'])
            if key in result and result[key]['existing'] != identifier:
                raise RuntimeError('Cannot reconstruct a conflicting stable character identity')
            result[key] = {'owner': owner, 'scope': branch, 'entity': ref['entityId'],
                'source': {'kind': 'reader', 'bookId': scope['book_id'], 'branchId': branch,
                    'entityId': ref['entityId']}, 'origin': None, 'existing': identifier}
    for row in bind.execute(sa.text(
            'SELECT owner_id,book_id,branch_id,data FROM knowledge_snapshots ORDER BY created_at,id')).mappings():
        data = row['data']; evidence = {e['id']: e for e in data.get('evidence', [])}
        def proof(character):
            selected = [evidence[k] for k in character.get('evidenceIds', []) if k in evidence]
            if not selected or character.get('identityUncertain') or character.get(
                    'identityProofVersion', data.get('identityProofVersion')) != 2:
                return None
            first = selected[0]; anchor = first.get('anchor', {}); quote = first.get('quote', '')
            label = character['displayName']; point = quote.find(label)
            if not label or point < 0 or not all(k in anchor for k in ('blockId', 'start', 'end')):
                return None
            return {'blockId': anchor['blockId'], 'start': anchor['start'] + len(quote[:point].encode('utf-16-le')) // 2,
                'originalLabel': label}
        for character in data.get('characters', []):
            key = (row['owner_id'], row['branch_id'], character['id'])
            if key in result:
                continue
            point = proof(character)
            if point and any(other['id'] != character['id'] and proof(other) == point for other in data.get('characters', [])):
                point = None
            matches = {ref['identityId'] for ref in sources.get((row['owner_id'], row['book_id']), {}).get('characterBindings', [])
                if point and ref.get('name') == point['originalLabel'] and ref.get('blockId') == point['blockId']
                and ref.get('start') == point['start']}
            source = {'kind': 'reader-proof', 'bookId': row['book_id'], 'branchId': row['branch_id'], **point} if point else {
                'kind': 'reader', 'bookId': row['book_id'], 'branchId': row['branch_id'], 'entityId': character['id']}
            result[key] = {'owner': row['owner_id'], 'scope': row['branch_id'], 'entity': character['id'],
                'source': source, 'origin': 'proof:' + hashlib.sha256(json.dumps(point, ensure_ascii=False,
                    sort_keys=True, separators=(',', ':')).encode()).hexdigest() if point else None,
                'existing': next(iter(matches)) if len(matches) == 1 else None}
    return result


def upgrade():
    for name, columns, unique in (
        ("character_identities", [sa.Column("origin_key", sa.String(256), nullable=False), sa.Column("source_ref", postgresql.JSONB(), nullable=False),
            sa.Column("ambiguous", sa.Boolean(), nullable=False)], ["owner_id", "origin_key"]),
        ("character_aliases", [sa.Column("identity_id", sa.String(32), sa.ForeignKey("character_identities.id", ondelete="CASCADE"), nullable=False),
            sa.Column("scope_kind", sa.String(20), nullable=False), sa.Column("scope_id", sa.String(32), nullable=False),
            sa.Column("entity_id", sa.String(100), nullable=False)], ["owner_id", "scope_kind", "scope_id", "entity_id"]),
        ("character_scope_revisions", [sa.Column("identity_id", sa.String(32), sa.ForeignKey("character_identities.id", ondelete="CASCADE"), nullable=False),
            sa.Column("scope_key", sa.String(100), nullable=False), sa.Column("scope_kind", sa.String(20), nullable=False),
            sa.Column("scope_id", sa.String(32), nullable=False), sa.Column("context", postgresql.JSONB(), nullable=False),
            sa.Column("visible_data", postgresql.JSONB(), nullable=False), sa.Column("version", sa.Integer(), nullable=False)], ["owner_id", "identity_id", "scope_key"]),
    ):
        op.create_table(name, sa.Column("id", sa.String(32), primary_key=True),
            sa.Column("owner_id", sa.String(32), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False), *columns, sa.UniqueConstraint(*unique))
        op.create_index("ix_" + name + "_owner_id", name, ["owner_id"])
        op.create_index("ix_" + name + "_created_at", name, ["created_at"])
    op.create_index("ix_character_aliases_identity_id", "character_aliases", ["identity_id"])
    op.create_index("ix_character_scope_revisions_identity_id", "character_scope_revisions", ["identity_id"])
    op.create_index("ix_character_scope_revisions_scope_id", "character_scope_revisions", ["scope_id"])
    op.add_column("role_sessions", sa.Column("character_identity_id", sa.String(32), nullable=True))
    op.create_foreign_key("fk_role_sessions_character_identity", "role_sessions", "character_identities", ["character_identity_id"], ["id"], ondelete="SET NULL")
    op.create_index("ix_role_sessions_character_identity_id", "role_sessions", ["character_identity_id"])
    bind = op.get_bind()
    metadata = sa.MetaData()
    identities = sa.Table("character_identities", metadata, autoload_with=bind)
    aliases = sa.Table("character_aliases", metadata, autoload_with=bind)
    seen = {}
    def occurrence(block_id, quote, label, start=0):
        point = quote.find(label)
        return {"blockId": block_id, "start": start + len(quote[:point].encode("utf-16-le")) // 2,
            "originalLabel": label} if label and point >= 0 else None
    def proof_origin(proof):
        return "proof:" + hashlib.sha256(json.dumps(proof, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()).hexdigest() if proof else None
    def add(owner, kind, scope, entity, source, origin=None, existing=None):
        key = (owner, kind, scope, entity)
        if key in seen:
            return seen[key]
        origin = origin or ("publication-reference:" + existing if existing else kind + ":" + scope + ":" + entity)
        if existing and uuid5(NAMESPACE_URL, "story-reader-character-v1:" + owner + ":" + origin).hex != existing:
            origin="publication-reference:"+existing
        identifier = existing or uuid5(NAMESPACE_URL, "story-reader-character-v1:" + owner + ":" + origin).hex
        present = bind.execute(sa.select(identities).where(identities.c.id == identifier)).mappings().first()
        if present and present["owner_id"] != owner:
            raise RuntimeError("Character reference must remain owner-scoped")
        if not present:
            bind.execute(postgresql.insert(identities).values(id=identifier, owner_id=owner, created_at=datetime.now(timezone.utc),
                origin_key=origin, source_ref=source, ambiguous=False).on_conflict_do_nothing())
        bind.execute(aliases.insert().values(id=uuid4().hex, owner_id=owner, created_at=datetime.now(timezone.utc),
            identity_id=identifier, scope_kind=kind, scope_id=scope, entity_id=entity))
        seen[key] = identifier
        return identifier
    for value in reader_reconstruction(bind).values():
        add(value['owner'], 'reader', value['scope'], value['entity'], value['source'],
            value['origin'], value['existing'])
    for row in bind.execute(sa.text("SELECT id,owner_id,book_id,branch_id,character_id FROM role_sessions ORDER BY created_at,id")).mappings():
        identity = add(row["owner_id"], "reader", row["branch_id"], row["character_id"],
            {"kind": "reader", "bookId": row["book_id"], "branchId": row["branch_id"], "entityId": row["character_id"]})
        bind.execute(sa.text("UPDATE role_sessions SET character_identity_id=:identity WHERE id=:id"), {"identity": identity, "id": row["id"]})
    if sa.inspect(bind).has_table("instances", schema="world"):
        for row in bind.execute(sa.text("SELECT id,owner_id,definition,source FROM world.instances ORDER BY created_at,id")).mappings():
            for entity in row["definition"].get("entities", []):
                if entity.get("type") != "character":continue
                proof=world_point(row['source'],row['definition'],entity)
                existing=world_reference(row['source'],entity,proof)
                if entity.get('sourceCharacterId') and not existing:
                    raise RuntimeError('Cannot reconstruct stable character identity from an invalid explicit source reference')
                anchor=row["source"].get("anchor") or {}
                source={"kind":"reader-proof","bookId":anchor.get("bookId"),"branchId":anchor.get("branchId"),**proof} if proof else {
                    "kind":"world","worldId":row["id"],"entityId":entity["id"]}
                add(row["owner_id"],"world",row["id"],entity["id"],source,proof_origin(proof),
                    existing=existing)


def downgrade():
    # A preparing world may first expose a unique proven person and later add
    # a homonym at that same occurrence. Runtime aliases intentionally keep the
    # UUID already given to the user. 0006 has nowhere to store that distinction:
    # reject a lossy rollback before any DDL rather than silently rebinding it.
    connection = op.get_bind()
    reconstructed = reader_reconstruction(connection)
    for row in connection.execute(sa.text('SELECT a.owner_id,a.scope_id,a.entity_id,a.identity_id '
            'FROM character_aliases a JOIN branches b ON b.id=a.scope_id AND b.owner_id=a.owner_id '
            "WHERE a.scope_kind='reader'")).mappings():
        value = reconstructed.get((row['owner_id'], row['scope_id'], row['entity_id']))
        origin = value['origin'] if value else None
        origin = origin or 'reader:' + row['scope_id'] + ':' + row['entity_id']
        expected = value['existing'] if value and value['existing'] else uuid5(NAMESPACE_URL,
            'story-reader-character-v1:' + row['owner_id'] + ':' + origin).hex
        if expected != row['identity_id']:
            raise RuntimeError('Cannot downgrade without losing a stable character identity; retain reader0007')
    if sa.inspect(connection).has_table("instances", schema="world"):
        rows = connection.execute(sa.text("""SELECT a.entity_id,a.identity_id,a.owner_id,a.scope_id,w.source,w.definition
            FROM character_aliases a JOIN world.instances w ON w.id=a.scope_id AND w.owner_id=a.owner_id
            WHERE a.scope_kind='world'""")).mappings()
        for row in rows:
            entity=next((e for e in row['definition'].get('entities',[]) if e['type']=='character' and e['id']==row['entity_id']),None)
            if not entity:
                continue  # Historical game entities retain their deterministic scoped origin.
            proof=world_point(row['source'],row['definition'],entity)
            existing=world_reference(row['source'],entity,proof)
            if entity.get('sourceCharacterId') and not existing:
                raise RuntimeError('Cannot downgrade without losing a stable character identity; retain reader0007')
            origin='proof:'+hashlib.sha256(json.dumps(proof,ensure_ascii=False,sort_keys=True,separators=(',',':')).encode()).hexdigest() if proof else 'world:'+row['scope_id']+':'+entity['id']
            expected=existing or uuid5(NAMESPACE_URL,'story-reader-character-v1:'+row['owner_id']+':'+origin).hex
            if expected!=row['identity_id']:
                raise RuntimeError('Cannot downgrade without losing a stable character identity; retain reader0007')
    op.drop_column("role_sessions", "character_identity_id")
    for table in ("character_scope_revisions", "character_aliases", "character_identities"):
        op.drop_table(table)
