"""Read-only domain checks before a backup is presented as confirmable.

No session, storage writes, source repairs or reference guessing are permitted.
The restore adapter independently revalidates after confirmation.
"""
from .package import validate_package_files
from .domain_schema import SPEC_BY_KIND
from .restore import _fields, _reference_closure, _relationships
from .manifest import pointer_get
from .world_scenes import scenes, check_hashes
from .world_schema import definition, checkpoint
from .errors import ArchiveError
from ..world.references import reference_problems
from .prose_history import validate_checkpoint, validate_generation_rows
from . import stream_history


def preflight_worlds(prepared):
    files = {entry['path']: entry for entry in prepared.manifest['files']}
    documents = validate_package_files(prepared.files_root, prepared.manifest, prepared.limits)
    ids = {}
    for item in prepared.manifest['objects']:
        ids.setdefault(item['type'], set()).add(item['id'])
    records = [(item, pointer_get(documents[item['path']], item.get('idPointer', '/id').rsplit('/', 1)[0]))
               for item in prepared.manifest['objects']]
    for item, record in records:
        if not isinstance(record, dict) or not isinstance(record.get('fields'), dict):
            raise ArchiveError('BACKUP_ROW_FIELDS_INVALID', {'objectType': item['type'], 'objectId': item['id'], 'path': '/fields'})
    tokens_by_world, token_worlds, scene_metadata, rows = {}, {}, {}, {}
    profiles, contents, stream_contents = set(), {}, {}
    task_types = {record['id']: record.get('fields', {}).get('task_type')
                  for item, record in records if item['type'] == 'task'}
    for item, record in records:
        if item['type'] == 'world_event_token':
            world_id = record.get('fields', {}).get('worldId')
            if record.get('rowType') != 'semanticEvent' or world_id not in ids.get('world', set()):
                raise ArchiveError('BACKUP_REFERENCE_UNDECLARED')
            token_worlds[item['id']] = world_id
            tokens_by_world.setdefault(world_id, set()).add(item['id'])
        elif item['type'] == 'world_scene':
            scene_metadata[item['id']] = record.get('fields', {})
        elif item['type'] == 'historical_profile':
            if record.get('rowType') != 'historicalProfile' or record['fields'] != {'historical': True}:
                raise ArchiveError('BACKUP_GENERATION_PROFILE_INVALID')
            profiles.add(item['id'])
        elif item['type'] == 'chapter_content':
            value = record['fields']
            if (record.get('rowType') != 'candidateContent' or set(value) != {'generationId', 'chapterIndex'}
                    or value['generationId'] not in ids.get('generation_batch', set())
                    or type(value['chapterIndex']) is not int or not 1 <= value['chapterIndex'] <= 10):
                raise ArchiveError('BACKUP_GENERATION_INVALID')
            contents[item['id']] = value
        elif item['type'] == 'stream_content':
            value = record['fields']
            if (record.get('rowType') != 'publicStreamContent' or set(value) != {'taskId', 'chapterIndex'}
                    or value['taskId'] not in ids.get('task', set())
                    or type(value['chapterIndex']) is not int or not 1 <= value['chapterIndex'] <= 20):
                stream_history.invalid()
            stream_contents[item['id']] = value
    checked = 0
    for document in documents.values():
        for record in document.get('rows', []):
            kind = {'World': 'world', 'WorldCheckpoint': 'checkpoint'}.get(record.get('rowType'))
            if not kind:
                continue
            context = {'objectType': kind, 'objectId': record.get('id'), 'path': '/definition'}
            try:
                values = _fields(SPEC_BY_KIND[kind], record, prepared.files_root, files)
                if kind == 'world' and values['status'] != 'active':
                    continue
                raw = values['definition']
                if isinstance(raw, dict) and isinstance(raw.get('entities'), list) and 'location' in raw:
                    problems = reference_problems(raw)
                    if problems:
                        raise ArchiveError('BACKUP_WORLD_STATE_INVALID', {**context, 'references': problems[:100], 'total': len(problems)})
                if kind == 'checkpoint':
                    context['path'] = '/state'
                    checkpoint(values['state'], raw, event_tokens=tokens_by_world.get(values['world_id'], set()))
                else:
                    definition(raw)
                checked += 1
            except ArchiveError as error:
                if not error.details:
                    error.details = context
                raise
            except (KeyError, TypeError, ValueError, AttributeError):
                raise ArchiveError('BACKUP_WORLD_STATE_INVALID', context)
    # Reuse the restore adapter's pure graph validators before any write. The
    # objects below are detached in-memory values, never added to a SQL session.
    for item, record in records:
        spec = SPEC_BY_KIND.get(item['type'])
        if spec is None:
            continue
        if record.get('rowType') != spec.model.__name__:
            if (item['type'], record.get('rowType')) in {('paragraph', 'frozenParagraph'), ('asset', 'asset')}:
                continue
            raise ArchiveError('BACKUP_ROW_TYPE_INVALID', {'objectType': item['type'], 'objectId': item['id'], 'path': '/rowType'})
        context = {'objectType': item['type'], 'objectId': item['id'], 'path': '/fields'}
        try:
            values = _fields(spec, record, prepared.files_root, files)
            if spec.kind=='character_artwork':
                from .character_artworks import decode as decode_artwork
                values.update(decode_artwork(record,prepared.files_root,files))
            _reference_closure(values, spec, ids, task_types)
            if spec.kind == 'task':
                validate_checkpoint(values['checkpoint'])
            if spec.kind == 'world' and values['source_book_id'] is None and values['source'].get('kind') == 'book':
                # Match the restore adapter's declared detached-source view.
                # _fields decoded a private copy: original package stays exact.
                values['source']['anchor'] = None
                values['source']['sourceStatus'] = 'source-not-imported'
            if spec.kind == 'world_turn':
                candidates = scenes(values['result'])
                check_hashes(candidates)
                for index, candidate in enumerate(candidates):
                    context['path'] = f'/result/chapters/{index}/state'
                    checkpoint(candidate['state'], candidate['definition'], event_tokens=tokens_by_world.get(values['world_id'], set()))
            rows.setdefault(spec.kind, {})[item['id']] = spec.model(id=item['id'], **values)
        except ArchiveError as error:
            if not error.details:
                error.details = context
            raise
        except (KeyError, TypeError, ValueError, AttributeError):
            raise ArchiveError('BACKUP_ROW_INVALID', context)
    try:
        _relationships(rows, token_worlds, scene_metadata, refresh_anchors=False)
        from .character_artworks import validate as validate_artworks
        validate_artworks(rows)
        validate_generation_rows(rows, profiles, contents)
        stream_history.validate_restored(rows, stream_contents)
    except ArchiveError as error:
        if not error.details:
            error.details = {'path': '/objects', 'phase': 'reference-graph'}
        raise
    except (KeyError, TypeError, ValueError, AttributeError):
        raise ArchiveError('BACKUP_REFERENCE_UNDECLARED', {'path': '/objects', 'phase': 'reference-graph'})
    return {'worldObjectsChecked': checked, 'domainObjectsChecked': sum(len(value) for value in rows.values())}
