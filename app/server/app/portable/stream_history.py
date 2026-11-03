"""Public role/world text history, with typed IDs and no executable journal."""
from copy import deepcopy
import re
from ..domain import digest, anchor_order
from .errors import ArchiveError

ID=re.compile(r"^[0-9a-f]{32}$")
BASE={"contentId","chapterIndex","streamVersion","snapshotVersion","version","text","contentHash","status","durability","historical"}
ROLE={"sessionId","turnId","userMessage","reviewWarnings"}
WORLD={"stage"}


def invalid():
    raise ArchiveError("BACKUP_STREAM_HISTORY_INVALID")


def stream(value,key,*,historical=False):
    if not isinstance(value,dict):invalid()
    allowed=BASE|(ROLE if key=="replyStream" else WORLD)
    v={k:deepcopy(item) for k,item in value.items() if k in allowed}
    required=BASE-{"historical"}
    if not required<=set(v) or not isinstance(v["contentId"],str) or not ID.fullmatch(v["contentId"]):invalid()
    for k in ("chapterIndex","streamVersion","snapshotVersion","version"):
        if type(v[k]) is not int or not 1<=v[k]<=9007199254740991:invalid()
    if v["chapterIndex"]>20 or v["version"]!=v["snapshotVersion"]:invalid()
    if not isinstance(v["text"],str) or len(v["text"])>8*1024*1024:invalid()
    try:v["text"].encode("utf-8",errors="strict")
    except UnicodeError:invalid()
    if v["contentHash"]!=digest(v["text"]) or not isinstance(v["status"],str) or v["status"] not in {"streaming","complete","interrupted","stopped"}:invalid()
    if not isinstance(v["durability"],str) or v["durability"] not in {"journaled","saved","unconfirmed"} or "historical" in v and type(v["historical"]) is not bool:invalid()
    if key=="replyStream":
        if v["chapterIndex"]!=1 or not isinstance(v.get("sessionId"),str) or not ID.fullmatch(v["sessionId"]):invalid()
        for k,limit in (("turnId",160),("userMessage",24000)):
            if not isinstance(v.get(k),str) or not 0<len(v[k])<=limit:invalid()
        if "reviewWarnings" in v and (not isinstance(v["reviewWarnings"],list) or len(v["reviewWarnings"])>100 or any(not isinstance(x,str) or len(x)>1000 for x in v["reviewWarnings"])):invalid()
    else:
        stage=v.get("stage")
        if not isinstance(stage,str):invalid()
        match=re.fullmatch(r"generation:(?:scene:([1-9][0-9]*)|rewrite)",stage)
        if stage=='generation':
            if v['chapterIndex']!=1:invalid()
        elif not match or int(match[1] or 1)!=v["chapterIndex"]:invalid()
    if historical:v.update(historical=True,durability="unconfirmed")
    return v


def task_objects(task):
    if any(not isinstance(getattr(task,key,None),dict) for key in ("payload","result","checkpoint")):
        raise ArchiveError("BACKUP_TASK_HISTORY_INVALID")


def safe_streams(task):
    task_objects(task)
    output={}
    for key in ("replyStream","worldStream"):
        value=task.checkpoint.get(key)
        # Imported copies are inert history and never read an executable source
        # journal. Authorized live tasks can have a newer atomic public receipt
        # than the repeatable-read database mirror after a failed commit.
        if not task.payload.get("importedHistory") and not (isinstance(value,dict) and value.get("historical")):
            if key=="replyStream" and task.task_type=="role_chat" or key=="worldStream" and task.task_type.startswith("world_"):
                from ..durable_stream import overlay
                from ..errors import Problem
                try:value=overlay(task,key)
                except (Problem,OSError,ValueError,TypeError,AttributeError,KeyError):invalid()
        # Old installations omitted the typed stream counters. Keep their old
        # export contract; typed streams must validate rather than be discarded.
        if value is not None and isinstance(value,dict) and "contentId" in value:
            if key=="replyStream" and task.task_type!="role_chat" or key=="worldStream" and not task.task_type.startswith("world_"):invalid()
            if key=="replyStream" and (value.get("sessionId")!=task.payload.get("sessionId") or value.get("turnId")!=task.payload.get("turnId")):invalid()
            output[key]=stream(value,key,historical=True)
            if key=='worldStream' and (value.get('stage')=='generation')!=(task.task_type=='world_character_chat'):invalid()
    return output


def register(selected,writer,reader):
    slots={};tasks={t.id:t for t in selected[reader.Task]};projections={}
    def add(identifier,task_id,index):
        if not isinstance(identifier,str) or not ID.fullmatch(identifier) or type(index) is not int or not 1<=index<=20:invalid()
        slot={"taskId":task_id,"chapterIndex":index}
        if identifier in slots and slots[identifier]!=slot:invalid()
        slots[identifier]=slot
    for task in tasks.values():
        # One public projection per snapshot, shared by registry and row writer.
        # It belongs to this export only; a later snapshot reads a fresh receipt.
        projections[task.id]=safe_streams(task)
        for value in projections[task.id].values():add(value["contentId"],task.id,value["chapterIndex"])
    for event in selected[reader.TaskEvent]:
        task=tasks[event.task_id]
        if task.task_type!="role_chat" and not task.task_type.startswith("world_"):continue
        value=event.payload
        if isinstance(value,dict) and "contentId" in value:add(value["contentId"],task.id,value.get("chapterIndex"))
    for identifier,slot in sorted(slots.items()):writer.add("stream_content",identifier,"publicStreamContent",slot)
    return projections


def validate_restored(rows,slots):
    used=set()
    for value in slots.values():
        task=rows.get("task",{}).get(value["taskId"])
        if not task or task.task_type!="role_chat" and not task.task_type.startswith("world_") or task.task_type=="role_chat" and value["chapterIndex"]!=1:invalid()
    for tid,task in rows.get("task",{}).items():
        task_objects(task)
        for key in ("replyStream","worldStream"):
            if key not in task.checkpoint:continue
            value=stream(task.checkpoint[key],key,historical=True)
            if slots.get(value["contentId"])!={"taskId":tid,"chapterIndex":value["chapterIndex"]}:invalid()
            if key=="replyStream" and (task.task_type!="role_chat" or value["sessionId"]!=task.payload.get("sessionId") or value["turnId"]!=task.payload.get("turnId")):invalid()
            if key=="replyStream":
                session=rows.get("role_session",{}).get(value["sessionId"])
                anchor=task.payload.get("anchor")
                if not session or session.owner_id!=task.owner_id or task.payload.get("bookId",session.book_id)!=session.book_id:invalid()
                if anchor is not None and (not isinstance(anchor,dict) or any(anchor.get(k)!=v for k,v in (
                        ("bookId",session.book_id),("branchId",session.branch_id),("revisionId",session.pinned_revision_id)))):invalid()
                if anchor is not None:
                    from ..errors import Problem
                    try:
                        revision=rows["revision"][session.pinned_revision_id]
                        if anchor_order(revision,anchor)>anchor_order(revision,session.pinned_anchor):invalid()
                    except (Problem,KeyError,TypeError):invalid()
            if key=="worldStream" and not task.task_type.startswith("world_"):invalid()
            if key=='worldStream' and (value.get('stage')=='generation')!=(task.task_type=='world_character_chat'):invalid()
            used.add(value["contentId"])
            task.checkpoint={**task.checkpoint,key:value}
    for event in rows.get("task_event",{}).values():
        if not isinstance(event.payload,dict) or event.payload.get("contentId") not in slots:continue
        if slots[event.payload["contentId"]]!={"taskId":event.task_id,"chapterIndex":event.payload.get("chapterIndex")}:invalid()
        used.add(event.payload["contentId"])
    if used!=set(slots):invalid()
