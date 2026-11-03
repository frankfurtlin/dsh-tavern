"""Immutable accepted paragraph bindings to a candidate's original UTF16 body."""
from .accepted_projection import project_accepted_text
from ..domain import digest


def create_accepted_proof(text, content_id, chapter_index, chapter_id, block_ids):
    parts = project_accepted_text(text, 'server-v1')['segments']
    if not parts or len(parts) != len(block_ids) or len(set(block_ids)) != len(block_ids):
        raise ValueError('accepted proof paragraphs')
    result = {'schema': 'accepted-content-proof-v1', 'style': 'server-v1',
              'contentId': content_id, 'chapterIndex': chapter_index, 'chapterId': chapter_id,
              'sourceHash': digest(text), 'segments': [dict(part, blockId=identifier,
                 blockHash=digest(part['text'])) for part, identifier in zip(parts, block_ids)]}
    validate_accepted_proof(result, text, content_id, chapter_index, chapter_id, block_ids)
    return result


def validate_accepted_proof(value, text, content_id, chapter_index, chapter_id, block_ids):
    expected = project_accepted_text(text, 'server-v1')['segments']
    if (not isinstance(value, dict) or set(value) != {'schema', 'style', 'contentId',
            'chapterIndex', 'chapterId', 'sourceHash', 'segments'}
            or value['schema'] != 'accepted-content-proof-v1' or value['style'] != 'server-v1'
            or value['contentId'] != content_id or type(value['chapterIndex']) is not int
            or value['chapterIndex'] != chapter_index or value['chapterId'] != chapter_id
            or any(not isinstance(item, str) or not 0 < len(item) <= 100 for item in
                   [content_id, chapter_id, *block_ids])
            or type(chapter_index) is not int or not 1 <= chapter_index <= 200
            or value['sourceHash'] != digest(text) or not isinstance(value['segments'], list)
            or not expected or len(expected) != len(block_ids)
            or len(value['segments']) != len(expected) or len(set(block_ids)) != len(block_ids)):
        raise ValueError('accepted proof envelope')
    for part, wanted, identifier in zip(value['segments'], expected, block_ids):
        bound = dict(wanted, blockId=identifier, blockHash=digest(wanted['text']))
        if (not isinstance(part, dict) or set(part) != set(bound)
                or any(type(part[key]) is not type(item) or part[key] != item
                       for key, item in bound.items())):
            raise ValueError('accepted proof binding')
    return value
