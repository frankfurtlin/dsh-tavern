"""Keep public view ranges on surviving publications inside source deletion."""
from sqlalchemy import select
from .models import Idempotency
from .domain import validate_anchor
from .book_deletion import rebook


def detach_publication_views(db, owner, old_book, new_book, branches):
    from .comparison.common import exposure_ready, scope_key
    from .comparison.models import ComparisonExposure
    if not exposure_ready(db):
        return
    rows = db.scalars(select(ComparisonExposure).where(ComparisonExposure.owner_id == owner,
        ComparisonExposure.kind == 'book', ComparisonExposure.resource_id == old_book,
        ComparisonExposure.line_id.in_(branches)).with_for_update())
    for row in rows:
        anchor = rebook(row.anchor, old_book, new_book)
        branch, revision, _ = validate_anchor(db, anchor, owner)
        if branch.id != row.line_id or revision.id != row.node_id:
            from .errors import Problem
            raise Problem('COMPARE_RANGE_INVALID', '保留故事的查看范围与路径不一致', 409)
        row.resource_id = new_book
        row.anchor = anchor
        row.scope_key = scope_key('book', new_book, row.line_id, row.node_id)
        row.version += 1
    # Stable past request keys remain past receipts. The deleted book root is
    # no longer authorized; surviving world roots can still resolve the same
    # publication range. Neither root's history creates a new reveal or call.
    for row in db.scalars(select(Idempotency).where(Idempotency.owner_id == owner,
            Idempotency.scope.startswith('compare-reveal:')).with_for_update()):
        value = row.result
        if (value.get('kind') == 'book' and value.get('resourceId') == old_book and
                value.get('lineId') in branches):
            row.result = {**rebook(value, old_book, new_book), 'resourceId': new_book}
