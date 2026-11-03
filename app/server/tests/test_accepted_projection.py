"""Pure proof of original body coordinates, not accepted-source integration."""
from copy import deepcopy
import pytest
from app.domain import utf16_len, utf16_slice
from app.prose.accepted_projection import project_accepted_text, validate_accepted_projection

BODIES = [
    '第一章 灯下\n  甲🙂  \n\n\t乙。\t\n',
    '甲。\r\n  乙🙂。  \r丙。\n',
    '\ufeff甲。\u0085乙。\u2028丙。\u2029丁。',
    '  \n\n第一章 灯下\n\n  甲🙂。\n  \n乙。\n ',
    '# 第 4 章 新信\n甲。\n\n乙。',
    '第一章 ' + '🙂' * 50 + '\n正文。',
    '第一章 ' + '🙂' * 51 + '\n正文。',
    '甲。\v乙。\f丙。\x1c丁。\x1d戊。\x1e己。',
    ' \t\r\n\u00a0\u2000\ufeff',
]


def test_server_projection_matches_actual_existing_splitlines_acceptance():
    for body in BODIES:
        value = project_accepted_text(body, 'server-v1')
        assert [row['text'] for row in value['segments']] == [part for part in body.splitlines() if part.strip()]


def test_each_original_utf16_segment_is_exact_for_both_acceptance_styles():
    for body in BODIES:
        for style in ['local-v1', 'server-v1']:
            value = project_accepted_text(body, style)
            assert validate_accepted_projection(value, body, style) == value
            last = 0
            for row in value['segments']:
                assert row['sourceStart'] >= last
                assert utf16_slice(body, row['sourceStart'], row['sourceEnd']) == row['text']
                assert row['targetStart'] == 0 and row['targetEnd'] == utf16_len(row['text'])
                last = row['sourceEnd']


def test_original_positions_keep_heading_and_trimmed_unicode_prefix_length():
    value = project_accepted_text(BODIES[0], 'local-v1')
    assert value['segments'] == [
        {'text': '甲🙂', 'sourceStart': 9, 'sourceEnd': 12, 'targetStart': 0, 'targetEnd': 3},
        {'text': '乙。', 'sourceStart': 17, 'sourceEnd': 19, 'targetStart': 0, 'targetEnd': 2},
    ]


def test_heading_limit_counts_utf16_as_the_existing_client_does():
    # A space plus fifty emoji is 101 UTF16 units, so it remains body text.
    value = project_accepted_text(BODIES[5], 'local-v1')
    assert value['segments'][0]['text'].startswith('第一章 ')
    short = project_accepted_text('第一章' + '🙂' * 50 + '\n正文。', 'local-v1')
    assert [row['text'] for row in short['segments']] == ['正文。']


def test_recomputed_mapping_rejects_wrong_source_offsets_types_and_removed_text():
    value = project_accepted_text(BODIES[0], 'local-v1')
    for key, item in [('sourceStart', 10), ('sourceEnd', True), ('targetEnd', 1.0), ('text', '甲')]:
        bad = deepcopy(value); bad['segments'][0][key] = item
        with pytest.raises(ValueError):
            validate_accepted_projection(bad, BODIES[0], 'local-v1')
    bad = deepcopy(value); bad['segments'].pop()
    with pytest.raises(ValueError):
        validate_accepted_projection(bad, BODIES[0], 'local-v1')


def test_invalid_unicode_and_unknown_style_fail_without_model_or_storage():
    for body, style in [('\ud800', 'local-v1'), ('\udfff', 'server-v1'), ('正文。', 'unknown')]:
        with pytest.raises(ValueError):
            project_accepted_text(body, style)
