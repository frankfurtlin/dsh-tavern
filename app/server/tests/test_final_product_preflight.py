"""New-spec read-only preflight through real packages and owned PostgreSQL rows."""
from copy import deepcopy
import hashlib
import pytest
from sqlalchemy import select, func
from .test_portable_domain import domain, migrate_owned_database, encrypted
from app.db import SessionLocal
from app import models as m
from app.world import models as w
from app.portable.package import prepare_backup
from app.portable.preflight import preflight_worlds
from app.portable.errors import ArchiveError


@pytest.mark.parametrize('world,include_source', [(False, False), (True, False), (True, True)])
def test_final_preflight_valid_graph_is_readonly(domain, tmp_path, world, include_source):
    original=encrypted(domain,tmp_path,world=world,include_source=include_source)
    with original.open('rb') as source,prepare_backup(source,'synthetic-test-password',tmp_path/'preflight') as prepared:
        hashes={path.relative_to(prepared.files_root):hashlib.sha256(path.read_bytes()).hexdigest()
                for path in prepared.files_root.rglob('*') if path.is_file()}
        result=preflight_worlds(prepared)
        assert result['domainObjectsChecked']>0
        assert {path.relative_to(prepared.files_root):hashlib.sha256(path.read_bytes()).hexdigest()
                for path in prepared.files_root.rglob('*') if path.is_file()}==hashes
    with SessionLocal() as db:
        for model in (m.Book,m.Task,w.World,w.WorldCheckpoint):
            assert db.scalar(select(func.count()).select_from(model).where(model.owner_id==domain['target']))==0


@pytest.mark.parametrize('fault',['known','null-initial','non-place-location'])
def test_final_preflight_rejects_semantic_corruption_before_any_copy(domain,tmp_path,fault):
    with SessionLocal() as db:
        point=db.get(w.WorldCheckpoint,domain['checkpoint'])
        if fault in {'null-initial','non-place-location'}:
            definition=deepcopy(point.definition);definition['initial'][point.state['playerId']]=None;point.definition=definition
            if fault=='non-place-location':
                definition['initial'][point.state['playerId']]={'location':point.state['playerId']}
        else:
            state=deepcopy(point.state)
            if fault=='known':state['known'].append('not-in-this-world')
            point.state=state
        db.commit()
    original=encrypted(domain,tmp_path,world=True)
    with original.open('rb') as source,prepare_backup(source,'synthetic-test-password',tmp_path/'preflight') as prepared:
        hashes={path.relative_to(prepared.files_root):hashlib.sha256(path.read_bytes()).hexdigest()
                for path in prepared.files_root.rglob('*') if path.is_file()}
        with pytest.raises(ArchiveError) as denied:preflight_worlds(prepared)
        assert denied.value.code=='BACKUP_WORLD_STATE_INVALID'
        assert denied.value.details.get('path') in {'/definition','/state'}
        assert {path.relative_to(prepared.files_root):hashlib.sha256(path.read_bytes()).hexdigest()
                for path in prepared.files_root.rglob('*') if path.is_file()}==hashes
    with SessionLocal() as db:
        assert db.scalar(select(func.count()).select_from(w.World).where(w.World.owner_id==domain['target']))==0
