from app.prose.worker import local_completeness,quality_hints
import pytest

def test_short_repeated_and_template_warnings_are_separate_from_completeness():
    text='命运的齿轮开始转动。\n\n她握着信。\n\n她握着信。'
    assert local_completeness(text,'stop','standard')[0] is True
    assert {hint['code'] for hint in quality_hints(text,'standard')} == {'CHAPTER_TOO_SHORT','CHAPTER_REPETITIVE','CHAPTER_PROGRESS_UNCERTAIN'}

@pytest.mark.parametrize('reason',[None,'length','timeout','cancelled'])
def test_received_text_without_a_normal_finish_remains_partial(reason):
    assert local_completeness('她仍握着信。',reason,'short') == (False,'CHAPTER_INTERRUPTED')

def test_no_output_and_unprojected_control_payloads_cannot_be_accepted():
    assert local_completeness('  ','stop','short') == (False,'NO_OUTPUT')
    assert local_completeness('{"tool_call":"anything"}','stop','short') == (False,'CHAPTER_FORMAT')
