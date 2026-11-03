"""Called inside the existing source deletion transaction, never a worker."""
from sqlalchemy import delete, select
from ..models import Idempotency
from .models import ComparisonExposure
from .common import exposure_ready


def purge(db, owner, kind, resource_id, line_id=None):
    if not exposure_ready(db):
        return
    criteria = [ComparisonExposure.owner_id == owner, ComparisonExposure.kind == kind,
        ComparisonExposure.resource_id == resource_id]
    if line_id is not None:
        criteria.append(ComparisonExposure.line_id == line_id)
    db.execute(delete(ComparisonExposure).where(*criteria))
    for row in db.scalars(select(Idempotency).where(Idempotency.owner_id == owner,
        Idempotency.scope.startswith("compare-reveal:"))):
        value = row.result
        if value.get("kind") == kind and value.get("resourceId") == resource_id and (line_id is None or value.get("lineId") == line_id):
            row.result = {"deleted": True, "kind": kind, "resourceId": resource_id,
                "lineId": value.get("lineId"), "nodeId": value.get("nodeId")}
