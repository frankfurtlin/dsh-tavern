"""Discard unsupported coarse hints, never physical evidence or known targets."""
from copy import deepcopy


def scope(value,targets):
    if not isinstance(value,dict):return value
    result=deepcopy(value);ignored=[]
    for kind in ('fixtures','exits'):
        parts=result.get(kind)
        if not isinstance(parts,list):continue
        allowed={part['id'] for part in targets[kind]}
        kept=[]
        for part in parts:
            # Keep malformed candidates visible to the ordinary strict
            # validator. Only a well-formed unlisted ID is a discardable hint.
            if isinstance(part,dict) and isinstance(part.get('id'),str) and part['id'] not in allowed:
                ignored.append({'kind':kind,'id':part['id'],'reason':'unlisted-coarse-hint'})
            else:kept.append(part)
        result[kind]=kept
    if ignored:result['ignoredCoarseHints']=ignored
    return result
