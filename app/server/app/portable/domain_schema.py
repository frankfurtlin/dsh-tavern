"""Explicit domain field allowlists for portable data, never ORM mass loading."""
from dataclasses import dataclass

from .. import models as reader
from ..world import models as world
from ..assets.models import ImageAsset,AssetPlacement
from ..play_controls.models import PlayControl, MemoryRevision, WorldChatSession, WorldChatMessage
from ..scenes.models import CharacterArtwork,CharacterLook
from .playback_domain import row_specs


@dataclass(frozen=True)
class RowSpec:
    kind: str
    model: type
    fields: tuple[str, ...]
    foreign: dict[str, str]
    prose: tuple[str, ...] = ()


ROW_SPECS = (
    RowSpec('character_artwork',CharacterArtwork,('world_id','entity_id','source_kind','license_note','descriptor','image'),{'world_id':'world'}),
    RowSpec('character_look',CharacterLook,('world_id','entity_id','selected_artwork_id','version'),{'world_id':'world','selected_artwork_id':'character_artwork'}),
    # Provider choices/credentials and derived chat snapshots are deliberately
    # excluded. Restore rebuilds knowledge at the same frozen checkpoint and
    # uses the destination account's default model.
    RowSpec('world_chat', WorldChatSession, ('world_id','line_id','checkpoint_id','character_id','name','version','status','updated_at'), {'world_id':'world','line_id':'world_line','checkpoint_id':'checkpoint'}),
    RowSpec('world_chat_message', WorldChatMessage, ('session_id','task_id','turn_key','sequence','role','text','status','checkpoint_id','model_name'), {'session_id':'world_chat','task_id':'task','checkpoint_id':'checkpoint'}, ('text',)),
    RowSpec('play_control', PlayControl, ('scope_kind','scope_id','world_id','version','memory'), {'world_id':'world'}),
    RowSpec('play_memory_revision', MemoryRevision, ('control_id','version','memory'), {'control_id':'play_control'}),
    RowSpec("asset",ImageAsset,("purpose","source_kind","license_note","sha256","mime","width","height","origin_scope","crop","version"),{}),
    RowSpec("asset_ref",AssetPlacement,("asset_id","scope","purpose","content_id","paragraph_id","side","order","alt","version"),{"asset_id":"asset"}),
    RowSpec("book", reader.Book, ("title", "source_hash", "source_id", "original_branch_id", "original_revision_id", "status", "version", "source_label", "favorite", "tags"), {"source_id": "source", "original_branch_id": "branch", "original_revision_id": "revision"}),
    RowSpec("source", reader.SourceDocument, ("book_id", "file_hash", "format", "encoding", "parser_version", "source_map"), {"book_id": "book"}),
    RowSpec("branch", reader.Branch, ("book_id", "parent_branch_id", "fork_anchor", "name", "status", "head_revision_id", "origin_operation_id", "version"), {"book_id": "book", "parent_branch_id": "branch", "head_revision_id": "revision", "origin_operation_id": "task"}),
    RowSpec("revision", reader.Revision, ("branch_id", "parent_revision_id", "sequence", "manifest", "content_hash", "provenance"), {"branch_id": "branch", "parent_revision_id": "revision"}),
    RowSpec("paragraph", reader.ContentBlock, ("book_id", "chapter_id", "text", "content_hash", "source"), {"book_id": "book", "chapter_id": "chapter"}, ("text",)),
    RowSpec("progress", reader.Progress, ("book_id", "branch_id", "revision_id", "anchor", "max_observed_anchor", "version", "updated_at"), {"book_id": "book", "branch_id": "branch", "revision_id": "revision"}),
    RowSpec("note", reader.Bookmark, ("book_id", "branch_id", "revision_id", "anchor", "note", "version", "status"), {"book_id": "book", "branch_id": "branch", "revision_id": "revision"}, ("note",)),
    RowSpec("resume_target", reader.ResumeTarget, ("story_id", "mode", "context", "version", "updated_at"), {}),
    RowSpec("knowledge_snapshot", reader.KnowledgeSnapshot, ("book_id", "branch_id", "revision_id", "boundary", "scope_hash", "analyzer_version", "profile_version", "data", "completeness"), {"book_id": "book", "branch_id": "branch", "revision_id": "revision"}),
    RowSpec("analysis_chunk", reader.AnalysisChunk, ("book_id", "branch_id", "cache_hash", "data"), {"book_id": "book", "branch_id": "branch"}),
    RowSpec("character_identity", reader.CharacterIdentity, ("origin_key", "source_ref", "ambiguous"), {}),
    RowSpec("character_alias", reader.CharacterAlias, ("identity_id", "scope_kind", "scope_id", "entity_id"), {"identity_id": "character_identity"}),
    RowSpec("character_scope_revision", reader.CharacterScopeRevision, ("identity_id", "scope_key", "scope_kind", "scope_id", "context", "visible_data", "version"), {"identity_id": "character_identity"}),
    RowSpec("role_session", reader.RoleSession, ("book_id", "branch_id", "pinned_revision_id", "pinned_anchor", "character_id", "character_name", "character_identity_id", "snapshot_id", "identity_mode", "status", "follow_progress", "knowledge_mode", "card_id"), {"book_id": "book", "branch_id": "branch", "pinned_revision_id": "revision", "character_identity_id": "character_identity", "snapshot_id": "knowledge_snapshot"}),
    RowSpec("role_message", reader.RoleMessage, ("session_id", "turn_id", "sequence", "role", "text", "task_id", "status"), {"session_id": "role_session", "task_id": "task"}, ("text",)),
    RowSpec("task", reader.Task, ("task_type", "state", "payload", "result", "error_code", "result_outcome", "event_seq", "checkpoint"), {}),
    RowSpec("task_event", reader.TaskEvent, ("task_id", "seq", "type", "payload"), {"task_id": "task"}),
    RowSpec("candidate", reader.Candidate, ("task_id", "text", "content_hash", "partial", "persistence_state", "validation_status", "outcome", "status", "accepted_revision_id", "accept_result"), {"task_id": "task", "accepted_revision_id": "revision"}, ("text",)),
    RowSpec("generation_batch",reader.GenerationBatch,("task_id","book_id","origin_anchor","request","profile_snapshot","candidate_version","accepted_prefix","target_branch_id","last_accepted_revision_id","version_times"),{"task_id":"task","book_id":"book","target_branch_id":"branch","last_accepted_revision_id":"revision"}),
    RowSpec("chapter_version",reader.GenerationChapterVersion,("generation_id","candidate_version","chapter_index","content_id","title","text","content_hash","stream_version","snapshot_version","completeness","durability","hints"),{"generation_id":"generation_batch","content_id":"chapter_content"},("text",)),
    RowSpec("generation_acceptance",reader.GenerationAcceptance,("generation_id","candidate_version","prefix_count","revision_id","result"),{"generation_id":"generation_batch","revision_id":"revision"}),
    RowSpec("world", world.World, ("name", "source_book_id", "source_title", "source", "definition", "status", "version", "active_line_id", "updated_at"), {"source_book_id": "book", "active_line_id": "world_line"}),
    RowSpec("world_line", world.WorldLine, ("world_id", "name", "parent_line_id", "fork_checkpoint_id", "head_checkpoint_id", "status", "version", "reading_position", "camera"), {"world_id": "world", "parent_line_id": "world_line", "fork_checkpoint_id": "checkpoint", "head_checkpoint_id": "checkpoint"}),
    RowSpec("checkpoint", world.WorldCheckpoint, ("world_id", "line_id", "parent_checkpoint_id", "sequence", "title", "text", "action", "state", "definition", "options", "changes", "summary", "memory", "content_hash", "kind"), {"world_id": "world", "line_id": "world_line", "parent_checkpoint_id": "checkpoint"}, ("text", "action", "summary")),
    RowSpec("world_turn", world.WorldTurn, ("world_id", "line_id", "parent_checkpoint_id", "turn_key", "task_id", "action", "kind", "draft_text", "status", "checkpoint_id", "warnings", "result"), {"world_id": "world", "line_id": "world_line", "parent_checkpoint_id": "checkpoint", "task_id": "task", "checkpoint_id": "checkpoint"}, ("action", "draft_text")),
    RowSpec("world_event", world.WorldEvent, ("world_id", "checkpoint_id", "entity_id", "type", "data", "visible_to", "minute"), {"world_id": "world", "checkpoint_id": "checkpoint"}),
    RowSpec("memory", world.WorldMemory, ("world_id", "line_id", "checkpoint_id", "first_checkpoint_id", "last_checkpoint_id", "level", "text", "event_ids"), {"world_id": "world", "line_id": "world_line", "checkpoint_id": "checkpoint", "first_checkpoint_id": "checkpoint", "last_checkpoint_id": "checkpoint"}, ("text",)),
    RowSpec("world_publication", world.WorldPublication, ("world_id", "checkpoint_id", "book_id", "branch_id", "revision_id", "anchor"), {"world_id": "world", "checkpoint_id": "checkpoint", "book_id": "book", "branch_id": "branch", "revision_id": "revision"}),
) + row_specs(RowSpec)
SPEC_BY_KIND = {item.kind: item for item in ROW_SPECS}
SPEC_BY_MODEL = {item.model: item for item in ROW_SPECS}

# Persistent structural IDs are declared here. World-local entity/lore/promise
# keys are projected explicitly by world_ids, without altering names or prose.
JSON_FOREIGN = {
    'chatId':'world_chat',
    "prefixSourceBlockId": "paragraph", "prefixSourceRevisionId": "revision",
    "bookId": "book", "branchId": "branch", "readerBranchId": "branch", "revisionId": "revision", "parentRevisionId": "revision",
    "blockId": "paragraph", "chapterId": "chapter", "sourceId": "source", "worldId": "world",
    "lineId": "world_line", "worldLineId": "world_line", "checkpointId": "checkpoint",
    "parentCheckpointId": "checkpoint", "firstCheckpointId": "checkpoint", "lastCheckpointId": "checkpoint",
    "selectedAtCheckpointId": "checkpoint", "resolvedAtCheckpointId": "checkpoint",
    "taskId": "task", "reusedDirectorTaskId": "task", "reusedNarratorTaskId": "task", "turnId": "world_turn", "sessionId": "role_session",
    "identityId": "character_identity", "characterIdentityId": "character_identity", "sourceCharacterId": "character_identity",
    "scopeRevisionId": "character_scope_revision", "snapshotId": "knowledge_snapshot", "assetId": "asset",
    "acceptedRevisionId": "revision", "candidateId": "candidate", "generationId":"generation_batch","contentId":"chapter_content",
    "profileId":"historical_profile","expectedHeadRevisionId":"revision","headRevisionId":"revision","baseRevisionId":"revision",
    "lastAcceptedRevisionId":"revision","targetBranchId":"branch","originWorldId":"world",
}
