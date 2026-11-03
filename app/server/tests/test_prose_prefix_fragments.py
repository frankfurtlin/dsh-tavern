from copy import deepcopy
from types import SimpleNamespace as Row
import pytest
from app.domain import digest, utf16_len
from app.models import ContentBlock, Revision
from app.prose.prefix_fragments import SCHEMA, validate_revision_fragments, fragment_roots,create_fragment


def fixture_rows():
    source = Row(id='source', owner_id='own', book_id='book', chapter_id='chapter',
        text='甲😀乙尚未读到', content_hash=digest('甲😀乙尚未读到'), source='original', html=None)
    part = Row(id='part', owner_id='own', book_id='book', chapter_id='chapter',
        text='甲😀', content_hash=digest('甲😀'), source='original', html=None)
    parent = Row(id='parent', owner_id='own', parent_revision_id=None,
        manifest=[{'id':'chapter','title':'own','blocks':['source']}], provenance={'type':'import'})
    proof = {'blockId':'part','prefixSourceBlockId':'source','prefixSourceRevisionId':'parent',
        'limit':3,'sourceHash':source.content_hash,'prefixHash':part.content_hash}
    child = Row(id='child', owner_id='own', parent_revision_id='parent',
        manifest=[{'id':'chapter','title':'own','blocks':['part']}],
        provenance={'type':'ai','paragraphPrefixSchema':SCHEMA,'paragraphPrefixes':[proof]})
    rows = {(ContentBlock,'source'):source,(ContentBlock,'part'):part,(Revision,'parent'):parent,(Revision,'child'):child}
    return child, rows, lambda model, identifier: rows.get((model,identifier))


def test_exact_unicode_prefix_is_replayable_without_copying_future_text():
    child, rows, lookup = fixture_rows()
    assert validate_revision_fragments(child, lookup) == {'part':'source'}
    assert fragment_roots([child,rows[Revision,'parent']],lookup) == {'part':'source'}
    assert utf16_len(rows[ContentBlock,'part'].text) == 3
    assert '尚未读到' not in rows[ContentBlock,'part'].text


@pytest.mark.parametrize('fault',['half-surrogate','text','hash','owner','parent','membership','duplicate','html','marker','shape','bool'])
def test_forged_prefix_is_rejected(fault):
    child, rows, lookup = fixture_rows();proof=child.provenance['paragraphPrefixes'][0]
    if fault == 'half-surrogate':proof['limit']=2
    elif fault == 'text':rows[ContentBlock,'part'].text='伪造'
    elif fault == 'hash':proof['sourceHash']='0'*64
    elif fault == 'owner':rows[ContentBlock,'source'].owner_id='another'
    elif fault == 'parent':child.parent_revision_id='another'
    elif fault == 'membership':rows[Revision,'parent'].manifest[0]['blocks']=[]
    elif fault == 'duplicate':child.manifest[0]['blocks'].append('part')
    elif fault == 'html':rows[ContentBlock,'part'].html='<p>future secret</p>'
    elif fault == 'marker':child.provenance.pop('paragraphPrefixSchema')
    elif fault == 'shape':proof['unknown']='change'
    elif fault == 'bool':proof['limit']=True
    with pytest.raises(ValueError):validate_revision_fragments(child,lookup)


def test_nested_prefixes_normalize_to_one_original_without_name_or_text_matching():
    child, rows, lookup = fixture_rows();tiny=Row(**deepcopy(vars(rows[ContentBlock,'part'])));tiny.id='tiny';tiny.text='甲';tiny.content_hash=digest('甲');rows[ContentBlock,'tiny']=tiny
    next_revision=Row(id='next',owner_id='own',parent_revision_id='child',manifest=[{'id':'chapter','title':'own','blocks':['tiny']}],provenance={'type':'ai','paragraphPrefixSchema':SCHEMA,'paragraphPrefixes':[{'blockId':'tiny','prefixSourceBlockId':'part','prefixSourceRevisionId':'child','limit':1,'sourceHash':rows[ContentBlock,'part'].content_hash,'prefixHash':tiny.content_hash}]})
    assert fragment_roots([next_revision,child,rows[Revision,'parent']],lookup)=={'part':'source','tiny':'source'}


@pytest.mark.parametrize('fault',['owner','book','revision-owner','bytes','membership'])
def test_creation_refuses_invalid_source_before_any_write(fault):
    child,rows,_=fixture_rows();block=rows[ContentBlock,'source'];parent=rows[Revision,'parent'];writes=[]
    if fault=='owner':block.owner_id='another'
    elif fault=='book':block.book_id='another'
    elif fault=='revision-owner':parent.owner_id='another'
    elif fault=='bytes':block.text='不同的原文'
    elif fault=='membership':parent.manifest=[]
    with pytest.raises(ValueError):create_fragment(Row(add=lambda row:writes.append(row)),'own','book',parent,block,3)
    assert writes==[]
