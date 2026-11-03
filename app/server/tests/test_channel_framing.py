import json
import pytest
from app.errors import Problem
from app.scenes.dialogue import structured_segments


def test_complete_literal_newline_frames_preserve_quotes_and_internal_newlines():
    first={'kind':'narration','text':'你看见盒子{A}。\n其中写着“记住”。'}
    second={'kind':'speech','speakerId':'shopkeeper','text':'我收到你的便笺。'}
    text=json.dumps(first,ensure_ascii=False)+'\\n'+json.dumps(second,ensure_ascii=False)
    result=structured_segments(text,[{'id':'shopkeeper','name':'唐禾'}])
    assert result[0]['text']==first['text'] and result[1]['text']==second['text']
    assert result[1]['speakerId']=='shopkeeper'


@pytest.mark.parametrize('tail',['\\n{"kind":"speech","speakerId":"stranger","text":"未知发言"}', '\\n{"kind":"narration","text":"未完成', '\\n这是额外协议文本', ';{"kind":"narration","text":"不应接受"}'])
def test_framing_never_accepts_incomplete_records_or_unknown_speakers(tail):
    with pytest.raises(Problem):structured_segments('{"kind":"narration","text":"完整的一段。"}'+tail,[{'id':'known','name':'店主'}])


def test_literal_separator_inside_a_json_string_is_not_a_record_boundary():
    part={'kind':'narration','text':'屏幕显示 }\n{ 然后熄灭。'}
    assert structured_segments(json.dumps(part),[])[0]['text']==part['text']
