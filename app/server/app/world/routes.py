"""Resolve an ordinary journey over declared, directed place connections."""
from collections import deque
from ..errors import Problem


def journey_path(definition, origin, destination, *, correction=False):
    places = {e['id'] for e in definition['entities'] if e['type'] == 'place'}
    if destination not in places:
        raise Problem('WORLD_LOCATION_INVALID', '移动目标不是一个已知地点')
    routes = definition['rules'].get('routes', {})
    if origin == destination:
        return [destination]
    if correction or origin not in routes:
        return [p for p in (origin, destination) if p in places]
    queue = deque([[origin]])
    seen = {origin}
    while queue:
        path = queue.popleft()
        for neighbour in routes.get(path[-1], []):
            if neighbour not in places or neighbour in seen:
                continue
            candidate = path + [neighbour]
            if neighbour == destination:
                return candidate
            seen.add(neighbour)
            queue.append(candidate)
    raise Problem('WORLD_ROUTE_RULE', '当前没有通往目的地的可通行路线，请先解决路上的阻碍或选择已有出口')
