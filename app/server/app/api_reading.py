from datetime import timedelta, timezone
from fastapi import APIRouter, Depends, Query
from pydantic import Field
from sqlalchemy import select, func, cast, Date
from .db import get_db
from .models import Book, Branch, Bookmark, ReadingActivity, UserSettings, now
from .schemas import Strict
from .security import current_user
from .domain import owned, idempotent, anchor_label
from .api_books import paginate, get_revision
from .errors import Problem

router = APIRouter()


@router.get("/notebook")
def notebook(cursor: str | None = None, user=Depends(current_user), db=Depends(get_db)):
    rows, next_cursor = paginate(db, Bookmark, user.id, cursor, filters=(Bookmark.note != "", Bookmark.status != 'deleted'))
    items = []
    for row in rows:
        book = owned(db, Book, row.book_id, user.id)
        branch = owned(db, Branch, row.branch_id, user.id)
        items.append({"id": row.id, "anchor": row.anchor, "note": row.note, "status": row.status,
                      "version": row.version, "bookTitle": book.title, "branchName": branch.name,
                      "location": anchor_label(db, row.anchor, user.id), "createdAt": row.created_at.isoformat()})
    return {"items": items, "nextCursor": next_cursor}


class ActivityInput(Strict):
    eventId: str = Field(min_length=16, max_length=100, pattern=r"^[A-Za-z0-9_-]+$")
    bookId: str
    branchId: str
    revisionId: str
    seconds: int = Field(strict=True, ge=1, le=60)


@router.post("/reading-activity", status_code=201)
def activity(data: ActivityInput, user=Depends(current_user), db=Depends(get_db)):
    branch, _ = get_revision(db, data.branchId, data.revisionId, user.id)
    if branch.book_id != data.bookId:
        raise Problem("ANCHOR_INVALID", "阅读记录所属故事线不一致")
    owned(db, Book, data.bookId, user.id)
    def create():
        # All tabs/devices share this transaction lock. Overlapping reading time
        # is capped at elapsed wall time; background tabs cannot inflate totals.
        db.scalar(select(UserSettings).where(UserSettings.owner_id == user.id).with_for_update())
        latest = db.scalar(select(ReadingActivity).where(ReadingActivity.owner_id == user.id).order_by(ReadingActivity.created_at.desc()).limit(1))
        instant = now()
        seconds = data.seconds if latest is None else min(data.seconds, max(0, int((instant - latest.created_at).total_seconds())))
        row = ReadingActivity(owner_id=user.id, book_id=data.bookId, branch_id=branch.id, revision_id=data.revisionId,
                              event_id=data.eventId, seconds=seconds, created_at=instant)
        db.add(row)
        db.flush()
        return {"eventId": data.eventId, "recordedSeconds": seconds}
    return idempotent(db, user.id, "reading-activity", data.eventId, data.model_dump(), create)


@router.get("/reading-stats")
def stats(offsetMinutes: int = Query(default=480, ge=-720, le=840), user=Depends(current_user), db=Depends(get_db)):
    zone = timezone(timedelta(minutes=offsetMinutes))
    today = now().astimezone(zone).date()
    start = today - timedelta(days=6)
    local_day = cast(func.timezone("UTC", ReadingActivity.created_at) + timedelta(minutes=offsetMinutes), Date)
    totals = dict(db.execute(select(local_day, func.sum(ReadingActivity.seconds)).where(
        ReadingActivity.owner_id == user.id).group_by(local_day)).all())
    streak, day = 0, today if totals.get(today, 0) else today - timedelta(days=1)
    while totals.get(day, 0) > 0:
        streak += 1
        day -= timedelta(days=1)
    preference = db.get(UserSettings, user.id)
    return {"todaySeconds": totals.get(today, 0), "totalSeconds": sum(totals.values()), "streakDays": streak,
            "goalMinutes": preference.reading_goal_minutes, "goalVersion": preference.reading_goal_version,
            "bookCount": db.scalar(select(func.count()).select_from(Book).where(Book.owner_id == user.id)),
            "noteCount": db.scalar(select(func.count()).select_from(Bookmark).where(Bookmark.owner_id == user.id, Bookmark.note != "")),
            "week": [{"date": (start + timedelta(days=n)).isoformat(), "seconds": totals.get(start + timedelta(days=n), 0)} for n in range(7)]}


class GoalInput(Strict):
    expectedVersion: int = Field(ge=1)
    minutes: int = Field(strict=True, ge=5, le=240)


@router.put("/reading-goal")
def goal(data: GoalInput, user=Depends(current_user), db=Depends(get_db)):
    row = db.scalar(select(UserSettings).where(UserSettings.owner_id == user.id).with_for_update())
    if row.reading_goal_version != data.expectedVersion:
        raise Problem("GOAL_CONFLICT", "另一设备已更新目标，请刷新阅读记录", 409)
    row.reading_goal_minutes = data.minutes
    row.reading_goal_version += 1
    db.commit()
    return {"goalMinutes": row.reading_goal_minutes, "goalVersion": row.reading_goal_version}
