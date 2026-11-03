"""A complete novel cannot silently become a short opening summary."""
from pathlib import Path
import re
from app.scripts.service import blocks_for, groups_for, review_issues, validate
from app.scripts.schemas import ScriptInput
from app.scripts.compiler import compile_blueprint
from app.errors import Problem
from .test_script_breakdown import blueprint, world_fixture
import pytest


def test_complete_hound_has_all_fifteen_chapters_and_no_truncated_paragraphs():
    source=(Path(__file__).parents[1]/'app/scripts/samples/hound-baskervilles.txt').read_text('utf-8')
    assert len(source)>300000
    blocks=blocks_for(source)
    assert {b['chapterId'] for b in blocks}-{'script-full'}=={f'script-chapter-{i:03}' for i in range(1,16)}
    paragraphs=[p.strip() for p in re.split(r'\n\s*\n',source.replace('\r\n','\n')) if p.strip()]
    assert ''.join(b['text'] for b in blocks)==''.join(paragraphs)
    groups=groups_for(blocks)
    assert len(groups)>5
    assert [b['blockId'] for g in groups for b in g]==[b['blockId'] for b in blocks]
    assert ScriptInput(sampleId='hound-baskervilles').sampleId=='hound-baskervilles'
    assert ScriptInput(text=source).text==source


@pytest.mark.parametrize('text', ['第一章少年\n\n少年出门。\n\n第二章试炼\n\n少年登山。',
    'CHAPTER I.\nBeginning\n\nA traveller arrived.\n\nCHAPTER II.\nEnding\n\nThe traveller returned.',
    'Chapter 1.\nFirst book\n\nOpening.\n\nChapter 1.\nSecond book\n\nContinuation.'])
def test_common_headings_and_volume_resets_have_distinct_source_order_ids(text):
    assert {b['chapterId'] for b in blocks_for(text)}=={'script-chapter-001','script-chapter-002'}


def test_empty_or_fabricated_batch_notes_never_count_as_full_source_analysis():
    from app.scripts.service import validate_notes
    blocks=blocks_for('第一章来客\n\nThe visitor arrived carrying a stick.')
    valid={'analyzedBlockIds':[b['blockId'] for b in blocks], 'characters':[], 'places':[], 'items':[], 'clues':[],
           'events':[{'summary':'来客抵达','sourceBlockIds':[blocks[1]['blockId']],'quote':'visitor arrived carrying a stick'}], 'ending':None}
    assert validate_notes(valid,blocks)==valid
    from copy import deepcopy
    for fault in ('empty','missing','fabricated','foreign'):
        notes=deepcopy(valid)
        if fault=='empty':notes['events']=[]
        if fault=='missing':notes['analyzedBlockIds']=notes['analyzedBlockIds'][:1]
        if fault=='fabricated':notes['events'][0]['quote']='He found a diamond.'
        if fault=='foreign':notes['events'][0]['sourceBlockIds']=['S9999']
        with pytest.raises(Problem):validate_notes(notes,blocks)


def test_novel_coverage_issues_prevent_publishing_a_short_summary():
    world=world_fixture()
    world.source['script']={'scale':'novel'}
    world.source['blocks']=[{'blockId':f'S{i:04}','chapterId':f'script-chapter-{i:03}',
                            'text':'该章的完整内容。','source':'original'} for i in range(1,16)]
    value,compiled=validate(compile_blueprint(blueprint(),world.source['blocks']),world)
    issues=review_issues(value,compiled,world)
    assert any('尚未覆盖 14 章' in i for i in issues)
    assert any('十二个可游玩场景' in i for i in issues)
    assert any('八个递进任务' in i for i in issues)
    with pytest.raises(Problem):validate(value,world,publication=True)


def test_all_chapters_are_covered_by_distinct_source_based_scenes():
    world=world_fixture()
    world.source['script']={'scale':'novel'}
    world.source['blocks']=[{'blockId':f'S{i:04}','chapterId':f'script-chapter-{i:03}',
                            'text':'该章的完整内容。','source':'original'} for i in range(1,16)]
    raw=blueprint()
    raw['scenes']=[dict(id=f's{i}',title=f'场景{i}',summary='原作事件',placeId='street',
                       sourceBlockIds=[f'S{i:04}']) for i in range(1,16)]
    raw['scenes'][0]['characterIds']=['witness']
    raw['objectives']+= [dict(id=f'o{i}',title=f'深入调查{i}',description='找到现场线索',
        requires=[f'o{i-1}'],checks=[dict(kind='field',field=f'调查{i}',value='完成')],
        sourceBlockIds=[f'S{i:04}']) for i in range(4,9)]
    value,compiled=validate(compile_blueprint(raw,world.source['blocks']),world)
    assert review_issues(value,compiled,world)==[]
