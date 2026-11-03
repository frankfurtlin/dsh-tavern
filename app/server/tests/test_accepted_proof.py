from copy import deepcopy
import pytest
from app.domain import utf16_slice
from app.prose.accepted_proof import create_accepted_proof, validate_accepted_proof

TEXT = '  甲🙂。  \r\n\n\t乙。\t\r丙。\u2028'

def test_proof_binds_exact_original_body_to_server_paragraph_bytes():
    value = create_accepted_proof(TEXT, 'content', 1, 'chapter', ['p1', 'p2', 'p3'])
    assert validate_accepted_proof(value, TEXT, 'content', 1, 'chapter', ['p1', 'p2', 'p3']) == value
    assert [p['text'] for p in value['segments']] == ['  甲🙂。  ', '\t乙。\t', '丙。']
    for part in value['segments']:
        assert utf16_slice(TEXT, part['sourceStart'], part['sourceEnd']) == part['text']

@pytest.mark.parametrize('field,item',[('blockId','other'),('blockHash','0'*64),
    ('sourceStart',1),('targetEnd',True),('text','甲。'),('extra','invented')])
def test_proof_rejects_changed_actual_bindings(field, item):
    value = create_accepted_proof(TEXT, 'content', 1, 'chapter', ['p1', 'p2', 'p3'])
    value['segments'][0][field] = item
    with pytest.raises(ValueError):validate_accepted_proof(value, TEXT, 'content', 1, 'chapter', ['p1', 'p2', 'p3'])

@pytest.mark.parametrize('field,item',[('sourceHash','0'*64),('contentId','other'),
    ('chapterId','other'),('chapterIndex',True),('chapterIndex',1.0),('extra','invented')])
def test_proof_rejects_changed_candidate_envelope(field, item):
    value = create_accepted_proof(TEXT, 'content', 1, 'chapter', ['p1', 'p2', 'p3'])
    value[field] = item
    with pytest.raises(ValueError):validate_accepted_proof(value, TEXT, 'content', 1, 'chapter', ['p1', 'p2', 'p3'])

def test_proof_rejects_empty_duplicate_missing_or_reordered_paragraph_lists():
    for identifiers in [[],['p1'],['same','same','same']]:
        with pytest.raises(ValueError):create_accepted_proof(TEXT,'content',1,'chapter',identifiers)
    value = create_accepted_proof(TEXT, 'content', 1, 'chapter', ['p1', 'p2', 'p3'])
    with pytest.raises(ValueError):validate_accepted_proof(value,TEXT,'content',1,'chapter',['p2','p1','p3'])
    with pytest.raises(ValueError):create_accepted_proof(' \n','content',1,'chapter',['p1'])
