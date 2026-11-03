"""Completed public-paragraph playback, independent of reader position/world state."""
import re
from copy import deepcopy
from typing import Literal
from fastapi import APIRouter, Depends, Query, Request
from pydantic import Field, model_validator
from sqlalchemy import select, text
from .db import get_db
from .security import current_user
from .schemas import Strict
from .api_stories import StoryContext, story_root, validate_context, STORY_PATTERN, ID_PATTERN
from .api_auth import settings_result
from .models import (UserSettings, PlaybackPosition, Progress, Revision, ContentBlock,
    GenerationChapterVersion, Idempotency, now)
from .domain import (owned, digest, canonical, make_anchor, validate_anchor, anchor_order,
    utf16_len, idempotent)
from .errors import Problem

router = APIRouter()
PROTOCOL = 'reader-playback-v1'
DEFAULT_SPEECH = {'rate': 1, 'voiceId': '', 'sleepMinutes': 0, 'stopAtChapterEnd': False}
# Match speech-types.ts's ECMAScript /\n\s*\n/ and String.trim exactly.
# Python's whitespace set excludes BOM and includes NEL; either difference
# changes the public paragraph IDs/UTF16 anchors already emitted by the client.
SPEECH_WHITESPACE = ('\t\n\v\f\r \u00a0\u1680' +
    ''.join(chr(value) for value in range(0x2000, 0x200b)) +
    '\u2028\u2029\u202f\u205f\u3000\ufeff')
SPEECH_PARAGRAPH_BREAK = re.compile(r'\n[\t\n\v\f\r \u00a0\u1680\u2000-\u200a\u2028\u2029\u202f\u205f\u3000\ufeff]*\n')


class SpeechPreferences(Strict):
    rate: float = Field(ge=.75, le=2, allow_inf_nan=False, strict=True)
    voiceId: str = Field(max_length=500)
    sleepMinutes: int = Field(strict=True)
    stopAtChapterEnd: bool = Field(strict=True)

    @model_validator(mode='after')
    def supported_timer(self):
        if self.sleepMinutes not in (0, 5, 15, 30, 60):
            raise ValueError('Unsupported sleep timer')
        return self


class ReadingPreferences(Strict):
    expectedVersion: int = Field(strict=True, ge=1)
    speech: SpeechPreferences | None = None
    fontSize: int | None = Field(default=None, strict=True, ge=14, le=32)
    lineHeight: float | None = Field(default=None, ge=1.4, le=2.2, allow_inf_nan=False)
    theme: Literal['light', 'dark', 'system'] | None = None
    readingMode: Literal['scroll', 'slide', 'page'] | None = None
    readingWidth: int | None = Field(default=None, strict=True, ge=32, le=40)
    chapterLength: Literal['short', 'standard', 'long'] | None = None

    @model_validator(mode='after')
    def nonempty_patch(self):
        fields = self.model_fields_set - {'expectedVersion'}
        if not fields or any(getattr(self, name) is None for name in fields):
            raise ValueError('An explicit non-null preference is required')
        return self


class PlaybackInput(Strict):
    expectedVersion: int = Field(strict=True, ge=0)
    context: StoryContext
    kind: Literal['reader', 'world', 'draft']
    mode: Literal['read', 'draft']
    lineId: str = Field(pattern=ID_PATTERN)
    contentRevision: str = Field(min_length=1, max_length=80)
    paragraphId: str = Field(min_length=1, max_length=100)
    lastCompletedAnchor: dict
    completed: bool = Field(strict=True)
    ended: bool = Field(strict=True)
    expectedProgressVersion: int | None = Field(default=None, strict=True, ge=0)

    @model_validator(mode='after')
    def completed_only(self):
        if not self.completed or self.context.mode != 'read':
            raise ValueError('Only completed public reading segments can be saved')
        if self.mode != ('draft' if self.kind == 'draft' else 'read'):
            raise ValueError('Playback kind and mode disagree')
        if self.expectedProgressVersion is not None and self.kind != 'reader':
            raise ValueError('World and draft playback do not change formal reading progress')
        return self


def preference_result(row):
    return {**settings_result(row), 'editVersion': row.version,
        'speech': deepcopy(row.speech_preferences)}


@router.get('/settings/reading')
def reading_preferences(user=Depends(current_user), db=Depends(get_db)):
    return preference_result(db.get(UserSettings, user.id))


@router.patch('/settings/reading')
def update_reading_preferences(data: ReadingPreferences, user=Depends(current_user), db=Depends(get_db)):
    row = db.scalar(select(UserSettings).where(UserSettings.owner_id == user.id).with_for_update())
    if row.version != data.expectedVersion:
        raise Problem('SETTINGS_CONFLICT', '其他设备已更新阅读或听书设置', 409, preference_result(row))
    for field, column in (('fontSize', 'font_size'), ('lineHeight', 'line_height'), ('theme', 'theme'),
            ('readingMode', 'reading_mode'), ('readingWidth', 'reading_width'), ('chapterLength', 'chapter_length')):
        if field in data.model_fields_set:
            value = getattr(data, field)
            setattr(row, column, str(value) if field == 'lineHeight' else value)
    if data.speech is not None:
        row.speech_preferences = data.speech.model_dump()
    row.version += 1
    db.commit()
    return preference_result(row)


@router.get('/playback/capabilities')
def capabilities(user=Depends(current_user), db=Depends(get_db)):
    ready = bool(db.scalar(text("SELECT to_regclass('playback_positions')"))) and bool(db.scalar(text(
        "SELECT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_schema='public' "
        "AND table_name='user_settings' AND column_name='speech_preferences')")))
    return {'ready': ready, 'protocol': PROTOCOL}


def invalid(message='已完成段落与这份公开正文不匹配'):
    raise Problem('PLAYBACK_ANCHOR_INVALID', message)


def scope(db, owner, story_id, kind, mode, line_id, revision_id, *, context=None, lock=False):
    """Authorize all references before reading a position or an old receipt."""
    root = story_root(db, owner, story_id)
    if mode != ('draft' if kind == 'draft' else 'read'):
        invalid('听书模式不匹配')
    path = None; batch = None
    if kind == 'world':
        from .world.models import World, WorldLine
        from .world.service import require_line, checked_checkpoint
        from .world.state import ancestry
        probe = owned(db, WorldLine, line_id, owner)
        world, line = require_line(db, owner, probe.world_id, line_id)
        if lock:
            # Reader root lifecycle has already been acquired by story_root.
            owned(db, World, world.id, owner, lock=True)
            world, line = require_line(db, owner, world.id, line_id, lock=True)
        frozen = checked_checkpoint(db, owner, world.id, revision_id)
        live = checked_checkpoint(db, owner, world.id, line.head_checkpoint_id)
        path = ancestry(db, frozen, owner)
        if frozen.id not in {cp.id for cp in ancestry(db, live, owner)}:
            raise Problem('RESOURCE_NOT_FOUND', '这份正文不在所选世界线中', 404)
        candidate = context or StoryContext(storyId=story_id, mode='read', perspective='reader',
            entryOrigin='reader', worldId=world.id, worldLineId=line.id, checkpointId=frozen.id)
        if candidate.worldId != world.id or candidate.worldLineId != line.id or candidate.checkpointId not in {cp.id for cp in path}:
            invalid()
        value = validate_context(db, owner, story_id, candidate, root=root)
        book_id = None; world_id = world.id
    elif kind == 'reader':
        from .models import Branch
        line = owned(db, Branch, line_id, owner)
        revision = owned(db, Revision, revision_id, owner)
        if revision.branch_id != line.id:
            raise Problem('RESOURCE_NOT_FOUND', '这份正文不属于所选故事线', 404)
        first = owned(db, ContentBlock, revision.manifest[0]['blocks'][0], owner)
        candidate = context or StoryContext(storyId=story_id, mode='read', perspective='reader',
            entryOrigin='reader', bookId=line.book_id, readerBranchId=line.id,
            anchor=make_anchor(line.book_id, line.id, revision.id, first))
        if candidate.readerBranchId != line.id or not candidate.anchor or candidate.anchor.revisionId != revision.id:
            invalid()
        value = validate_context(db, owner, story_id, candidate, root=root)
        book_id = line.book_id; world_id = None
    else:
        from .prose.service import load
        match = re.fullmatch(r'([a-f0-9]{32}):([1-9][0-9]*)', revision_id)
        if not match:
            invalid('草稿版本无效')
        batch, task = load(db, match[1], owner)
        version = int(match[2])
        if str(version) not in batch.version_times or line_id != batch.origin_anchor['branchId']:
            raise Problem('RESOURCE_NOT_FOUND', '草稿不属于所选起点', 404)
        candidate = context or StoryContext(storyId=story_id, mode='read', perspective='reader',
            entryOrigin='reader', bookId=batch.book_id, readerBranchId=line_id, anchor=batch.origin_anchor)
        if (candidate.bookId != batch.book_id or candidate.readerBranchId != line_id or
                not candidate.anchor or candidate.anchor.model_dump() != batch.origin_anchor):
            invalid('草稿必须使用它固定的公开阅读起点')
        value = validate_context(db, owner, story_id, candidate, root=root)
        book_id = batch.book_id; world_id = None
    key = digest(canonical([kind, line_id, revision_id]))
    return {'key': key, 'context': value, 'bookId': book_id, 'worldId': world_id,
        'path': path, 'batch': batch, 'kind': kind, 'lineId': line_id, 'revisionId': revision_id}


def completion_order(db, owner, authorized, anchor, paragraph_id=None):
    kind = authorized['kind']; line_id = authorized['lineId']; rev_id = authorized['revisionId']
    if kind == 'reader':
        branch, revision, block = validate_anchor(db, anchor, owner, paragraph=True)
        if (branch.id != line_id or revision.id != rev_id or not block.text.strip(SPEECH_WHITESPACE) or
                anchor['end'] != utf16_len(block.text) or (paragraph_id is not None and paragraph_id != block.id)):
            invalid()
        return anchor_order(revision, anchor)
    if kind == 'world':
        required = {'worldId', 'worldLineId', 'checkpointId', 'paragraph', 'start', 'end', 'offsetUnit'}
        if (set(anchor) != required or anchor['worldId'] != authorized['worldId'] or anchor['worldLineId'] != line_id or
                anchor['offsetUnit'] != 'utf16' or any(type(anchor[k]) is not int for k in ('paragraph', 'start', 'end'))):
            invalid()
        from .world.service import checkpoint_result
        ids = [cp.id for cp in authorized['path']]
        if anchor['checkpointId'] not in ids:
            raise Problem('RESOURCE_NOT_FOUND', '此段落不属于所选历史正文', 404)
        cp = authorized['path'][ids.index(anchor['checkpointId'])]
        public_text = checkpoint_result(cp, include_state=False)['text']
        segments = []; cursor = 0
        for part in SPEECH_PARAGRAPH_BREAK.split(public_text):
            start = public_text.index(part, cursor); cursor = start + len(part)
            if part.strip(SPEECH_WHITESPACE): segments.append((utf16_len(public_text[:start]), utf16_len(public_text[:cursor])))
        number = anchor['paragraph']
        if (not 0 <= number < len(segments) or (anchor['start'], anchor['end']) != segments[number] or
                (paragraph_id is not None and paragraph_id != cp.id + ':' + str(number))):
            invalid()
        return ids.index(cp.id), number, anchor['end']
    required = {'bookId', 'generationId', 'contentId', 'candidateVersion', 'start', 'end', 'offsetUnit', 'draft'}
    batch = authorized['batch']; version = int(rev_id.split(':')[1])
    if (set(anchor) != required or anchor['bookId'] != batch.book_id or anchor['generationId'] != batch.id or
            type(anchor['candidateVersion']) is not int or anchor['candidateVersion'] != version or
            type(anchor['draft']) is not bool or anchor['draft'] is not True or anchor['offsetUnit'] != 'utf16' or
            type(anchor['start']) is not int or type(anchor['end']) is not int or anchor['start'] != 0):
        invalid()
    chapter = db.scalar(select(GenerationChapterVersion).where(GenerationChapterVersion.owner_id == owner,
        GenerationChapterVersion.generation_id == batch.id, GenerationChapterVersion.candidate_version == version,
        GenerationChapterVersion.content_id == anchor['contentId']))
    if (chapter is None or chapter.completeness != 'complete' or chapter.durability != 'saved' or
            not chapter.text.strip(SPEECH_WHITESPACE) or chapter.content_hash != digest(chapter.text) or
            anchor['end'] != utf16_len(chapter.text) or (paragraph_id is not None and paragraph_id != chapter.content_id)):
        invalid('只有完整、已保存且校验通过的草稿可以保存听书完成位置')
    return chapter.chapter_index, anchor['end']


def result(row, *, story_id=None):
    context = deepcopy(row.context)
    if story_id: context['storyId'] = story_id
    return {'version': row.version, 'position': {'context': context, 'kind': row.kind, 'mode': row.mode,
        'lineId': row.line_id, 'contentRevision': row.content_revision, 'paragraphId': row.paragraph_id,
        'lastCompletedAnchor': deepcopy(row.last_completed_anchor), 'maxHeardAnchor': deepcopy(row.max_heard_anchor),
        'ended': row.ended, 'completedAt': row.completed_at.isoformat(), 'updatedAt': row.updated_at.isoformat()}}


def row_for(db, owner, key, lock=False):
    q = select(PlaybackPosition).where(PlaybackPosition.owner_id == owner, PlaybackPosition.scope_key == key)
    return db.scalar((q.with_for_update() if lock else q).execution_options(populate_existing=True))


@router.get('/playback/position')
def read_position(storyId: str = Query(pattern=STORY_PATTERN), kind: Literal['reader', 'world', 'draft'] = Query(),
        mode: Literal['read', 'draft'] = Query(), lineId: str = Query(pattern=ID_PATTERN),
        contentRevision: str = Query(min_length=1, max_length=80), user=Depends(current_user), db=Depends(get_db)):
    authorized = scope(db, user.id, storyId, kind, mode, lineId, contentRevision)
    row = row_for(db, user.id, authorized['key'])
    if row is None: return {'version': 0, 'position': None}
    context = StoryContext.model_validate({**row.context, 'storyId': storyId})
    authorized = scope(db, user.id, storyId, kind, mode, lineId, contentRevision, context=context)
    completion_order(db, user.id, authorized, row.last_completed_anchor, row.paragraph_id)
    completion_order(db, user.id, authorized, row.max_heard_anchor)
    return result(row, story_id=storyId)


@router.put('/playback/position')
def save_position(data: PlaybackInput, request: Request, user=Depends(current_user), db=Depends(get_db)):
    authorized = scope(db, user.id, data.context.storyId, data.kind, data.mode, data.lineId,
        data.contentRevision, context=data.context, lock=True)
    order = completion_order(db, user.id, authorized, data.lastCompletedAnchor, data.paragraphId)
    # Serialize positions shared by multiple story entry roots without touching
    # Book/World versions, head, camera or a different mode's ResumeTarget.
    db.execute(text('SELECT pg_advisory_xact_lock(:value)'),
        {'value': int(digest(user.id + 'playback-scope:' + authorized['key'])[:15], 16)})
    def save():
        row = row_for(db, user.id, authorized['key'], lock=True)
        if data.expectedVersion != (row.version if row else 0):
            raise Problem('PLAYBACK_CONFLICT', '其他设备已更新听书位置，请保留本机完成位置后选择', 409,
                result(row, story_id=data.context.storyId) if row else {'version': 0, 'position': None})
        read_advance = 'not-requested'; progress_value = None
        if data.expectedProgressVersion is not None:
            revision = owned(db, Revision, data.contentRevision, user.id)
            progress = db.scalar(select(Progress).where(Progress.owner_id == user.id,
                Progress.branch_id == data.lineId, Progress.revision_id == revision.id).with_for_update())
            if data.expectedProgressVersion != (progress.version if progress else 0):
                raise Problem('PLAYBACK_PROGRESS_CONFLICT', '其他设备已更新已读范围，本次听书完成尚未保存', 409,
                    {'readerProgress': progress_result(progress) if progress else {'version': 0, 'anchor': None,
                        'maxObservedAnchor': None, 'revisionId': revision.id}})
            if progress is None:
                if anchor_order(revision, authorized['context']['anchor']) > order:
                    invalid('首次已播边界不能早于当前公开阅读起点')
                progress = Progress(owner_id=user.id, book_id=authorized['bookId'], branch_id=data.lineId,
                    revision_id=revision.id, anchor=deepcopy(authorized['context']['anchor']),
                    max_observed_anchor=deepcopy(data.lastCompletedAnchor), version=1, device_sequences={})
                db.add(progress); read_advance = 'advanced'
            elif anchor_order(revision, progress.max_observed_anchor) < order:
                progress.max_observed_anchor = deepcopy(data.lastCompletedAnchor)
                progress.version += 1; progress.updated_at = now(); read_advance = 'advanced'
            else:
                read_advance = 'already-observed'
            progress_value = progress_result(progress)
        instant = now()
        if row is None:
            row = PlaybackPosition(owner_id=user.id, scope_key=authorized['key'], story_id=data.context.storyId,
                kind=data.kind, mode=data.mode, line_id=data.lineId, content_revision=data.contentRevision,
                book_id=authorized['bookId'], world_id=authorized['worldId'],
                generation_id=authorized['batch'].id if authorized['batch'] else None,
                candidate_version=int(data.contentRevision.split(':')[1]) if data.kind == 'draft' else None,
                context=authorized['context'], paragraph_id=data.paragraphId, last_completed_anchor=deepcopy(data.lastCompletedAnchor),
                max_heard_anchor=deepcopy(data.lastCompletedAnchor), ended=data.ended, version=1,
                completed_at=instant, updated_at=instant)
            db.add(row)
        else:
            if order > completion_order(db, user.id, authorized, row.max_heard_anchor):
                row.max_heard_anchor = deepcopy(data.lastCompletedAnchor)
            row.context = authorized['context']; row.story_id = data.context.storyId; row.paragraph_id = data.paragraphId
            row.last_completed_anchor = deepcopy(data.lastCompletedAnchor); row.ended = data.ended
            row.version += 1; row.completed_at = row.updated_at = instant
        response = {**result(row), 'readAdvance': read_advance, 'requestId': request.headers.get('idempotency-key')}
        if progress_value is not None: response['readerProgress'] = progress_value
        return response
    return idempotent(db, user.id, 'playback-save', request.headers.get('idempotency-key'), data.model_dump(), save)


def progress_result(row):
    return {'version': row.version, 'anchor': deepcopy(row.anchor),
        'maxObservedAnchor': deepcopy(row.max_observed_anchor), 'revisionId': row.revision_id}


@router.get('/playback/request-status')
def request_status(requestId: str = Query(min_length=1, max_length=160), storyId: str = Query(pattern=STORY_PATTERN),
        user=Depends(current_user), db=Depends(get_db)):
    story_root(db, user.id, storyId)
    receipt = db.scalar(select(Idempotency).where(Idempotency.owner_id == user.id,
        Idempotency.scope == 'playback-save', Idempotency.key == requestId))
    if receipt is None: return {'found': False}
    value = receipt.result; position = value.get('position')
    if not position or position['context']['storyId'] != storyId:
        return {'found': False}
    context = StoryContext.model_validate(position['context'])
    authorized = scope(db, user.id, storyId, position['kind'], position['mode'], position['lineId'],
        position['contentRevision'], context=context)
    completion_order(db, user.id, authorized, position['lastCompletedAnchor'], position['paragraphId'])
    return {'found': True, 'receipt': value}
