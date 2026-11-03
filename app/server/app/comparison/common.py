from sqlalchemy import select,text
from ..domain import digest,canonical
from ..errors import Problem
from ..api_stories import story_root,relevant_books,related_worlds,publication_branches
from .models import ComparisonExposure


def authorize(db,owner,story_id,kind,resource_id,line_id=None):
    root_kind,root=story_root(db,owner,story_id)
    rows=relevant_books(db,root_kind,root,owner) if kind=="book" else related_worlds(db,root_kind,root,owner)
    if resource_id not in {row.id for row in rows}:raise Problem("RESOURCE_NOT_FOUND","比较路径不属于这篇故事",404)
    if root_kind=="world" and kind=="book" and line_id not in publication_branches(db,owner,root):
        raise Problem("RESOURCE_NOT_FOUND","阅读路径不属于这篇故事的发布家族",404)


def exposure_ready(db):return db.scalar(text("SELECT to_regclass('comparison_exposures')")) is not None


def scope_key(kind,resource,line,node):return digest(canonical([kind,resource,line,node]))


def exposures(db,owner,kind,resource,line):
    if not exposure_ready(db):return []
    return list(db.scalars(select(ComparisonExposure).where(ComparisonExposure.owner_id==owner,
        ComparisonExposure.kind==kind,ComparisonExposure.resource_id==resource,ComparisonExposure.line_id==line)))


def differences(left,right,limit=256):
    """Only compare already projected public values; unknown stays unknown."""
    result=[];pending=[("",left,right)]
    while pending:
        path,before,after=pending.pop()
        if before==after:continue
        if isinstance(before,dict) and isinstance(after,dict):
            pending.extend((path+"/"+key.replace("~","~0").replace("/","~1"),before.get(key),after.get(key)) for key in sorted(set(before)|set(after),reverse=True))
        else:
            result.append({"path":path,"before":before,"after":after,"beforeKnown":before is not None,"afterKnown":after is not None})
            if len(result)==limit:return result,True
    return result,False
