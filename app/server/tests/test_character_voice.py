"""Voice extraction never promotes narration into character world knowledge."""
import json
from types import SimpleNamespace
import pytest
from app.knowledge import character_voice

@pytest.mark.parametrize('predicate,expected',[('speaking_style',['concise','practical']),('secret_identity',[])])
def test_voice_only_exports_fixed_traits_without_free_form_secret(predicate,expected):
    snapshot=SimpleNamespace(data={'facts':[
        {'subjectId':'lin','status':'confirmed','temporalRelation':'at_scope','predicate':predicate,'value':'简短、行动导向，OMNISCIENT_PRIVATE_SECRET','evidenceIds':['voice-proof'],'knownBy':[]},
        {'subjectId':'other','status':'confirmed','temporalRelation':'at_scope','predicate':'speaking_style','value':'温和','evidenceIds':['other-proof']},
        {'subjectId':'lin','status':'confirmed','temporalRelation':'future','predicate':'speaking_style','value':'幽默','evidenceIds':['future-proof']},
        {'subjectId':'lin','status':'uncertain','temporalRelation':'at_scope','predicate':'speaking_style','value':'正式','evidenceIds':['uncertain-proof']} ]})
    result=character_voice(snapshot,'lin')
    assert result['traits']==expected
    assert result['evidenceIds']==(['voice-proof'] if expected else [])
    assert 'OMNISCIENT_PRIVATE_SECRET' not in json.dumps(result)
    assert snapshot.data['facts'][0]['knownBy']==[]

def test_negated_voice_descriptions_do_not_reverse_the_character_style():
    snapshot=SimpleNamespace(data={'facts':[{'subjectId':'lin','status':'confirmed','temporalRelation':'at_scope','predicate':'speaking_style','value':'说话简短，不温和，不够幽默，很少正式，但行动导向','evidenceIds':['voice-proof']}]})
    assert character_voice(snapshot,'lin')['traits']==['concise','practical']

def test_small_analysis_chunks_preserve_all_unicode_and_exact_source_ranges():
    from app.knowledge import chunks_for
    text='甲😀乙。'*401
    chunks=chunks_for([{'blockId':'long-block','text':text}])
    parts=[part for chunk in chunks for part in chunk]
    assert len(chunks)>1 and all(sum(len(part['text']) for part in chunk)<=400 for chunk in chunks)
    assert ''.join(part['text'] for part in parts)==text
    for part in parts:
        assert text[part['sourceStart']:part['sourceStart']+len(part['text'])]==part['text']
