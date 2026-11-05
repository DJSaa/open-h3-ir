"""Regressions for R04 / vllm-omni#8905: compiler-owned speech structure."""
import re
import pytest
from h3ir.language import prepare, restore_dialogue, language_findings
from h3ir.models import Brief, DialogueLine, SpeakerPlan
from h3ir.render import dialogue_markup


def example():
    brief = prepare(Brief(intent='图1说：“周末一起去看海，我们早点出发。”图2说：“好，我来准备。”'))
    speakers = [SpeakerPlan(sid='(S1)', subject='<Subject 1>', descriptor='图1', voice_ref='<Audio 1>'),
                SpeakerPlan(sid='(S2)', subject='<Subject 2>', descriptor='图2', voice_ref='<Audio 2>')]
    return brief, speakers


def test_render_does_not_leak_intake_alias_and_preserves_voice_and_exact_speech():
    brief, speakers = example()
    out = dialogue_markup(brief.dialogue[0], speakers[0])
    assert out == '<Subject 1> (S1), using the voice timbre referenced from <Audio 1>, says: <d>[Chinese] 周末一起去看海，我们早点出发。</d>'


@pytest.mark.parametrize('intro', [
    '<Subject 1> (S1) says, ',
    '<Subject 1> (S1), using the voice timbre referenced from <Audio 1>, says: ',
    '<Subject 2> (S2), using the voice timbre referenced from <Audio 2>, says: ',
    '图1 <Subject 1> (S1) says: ',
    '<Subject 1> (S1) says, <Subject 1> (S1) says: ',
])
def test_preintroduced_placeholder_is_one_canonical_utterance(intro):
    brief, speakers = example()
    result = restore_dialogue('At 00:02.000 '+intro+'{{D1}}', brief, speakers)
    expected = 'At 00:02.000 '+dialogue_markup(brief.dialogue[0],speakers[0])
    assert result == expected
    assert result.count('<Audio 1>') == 1
    assert result.count('周末一起去看海，我们早点出发。') == 1


def test_delivery_and_offscreen_cut_not_lost_by_normalization():
    brief,speakers=example()
    text='[Shot 1] At 00:02.000 <Subject 1> (S1) softly says: {{D1}} <scenetrans> [Shot 2] At 00:04.000 The listener keeps their lips closed while the same line finishes off-screen. <Subject 2> (S2) replies: {{D2}}'
    out=restore_dialogue(text,brief,speakers)
    assert 'softly says: <d>' in out and 'replies: <d>' in out
    assert '<scenetrans> [Shot 2] At 00:04.000 The listener keeps their lips closed while the same line finishes off-screen.' in out
    assert out.count('(S1)')==1 and out.count('(S2)')==1


def test_protected_dialogue_cannot_be_treated_as_structural_prefix():
    line='他说：<Subject 1> (S1) says, {{D2}}，这是一句原话。'
    brief=Brief(intent='',dialogue=[DialogueLine(line,language='Chinese'),DialogueLine('好',language='Chinese')])
    out=restore_dialogue('{{D1}} {{D2}}',brief)
    assert f'<d>[Chinese] {line}</d>' in out
    assert out.count('<d>')==2


def test_unrecognized_action_is_never_greedily_erased():
    brief,speakers=example()
    prefix='[Shot 1] <Subject 1> (S1) moves toward the desk, pauses, and nods. '
    out=restore_dialogue(prefix+'{{D1}}',brief,speakers)
    assert out.startswith(prefix)


def test_base_mode_pronoun_and_original_speech_stay_intact():
    brief=prepare(Brief(intent='她说：“准备好了，现在开始。”'))
    out=restore_dialogue('She says: {{D1}}',brief)
    assert out=='She (S1) says: <d>[Chinese] 准备好了，现在开始。</d>'


def test_offscreen_voice_preserved_without_chinese_alias():
    brief,speakers=example()
    line=DialogueLine('你好。',language='Chinese',speaker_hint='图1',voiceover=True)
    out=dialogue_markup(line,speakers[0])
    assert 'from <Audio 1>' in out and 'off-screen voiceover' in out
    assert '图1' not in out and '<d>[Chinese] 你好。</d>' in out


@pytest.mark.parametrize('hint',['甲','图2','年轻女士'])
def test_base_mode_chinese_hint_is_metadata_not_output(hint):
    line=DialogueLine('你好。',language='Chinese',speaker_hint=hint)
    out=dialogue_markup(line,SpeakerPlan(sid='(S1)',subject=None,descriptor=hint))
    assert out=='(S1) says: <d>[Chinese] 你好。</d>'


@pytest.mark.parametrize('leak',['图1','“图1”','"缓慢走向门口"','[图1]','<图1>'])
def test_short_or_quoted_han_direction_cannot_hide_in_long_english_prose(leak):
    text=f'detailed_description: [Shot 1] A person stands in a quiet room with soft natural lighting. {leak} then approaches the door.'
    assert any(f.rule=='DINGO-description-language' and f.severity=='ERROR' for f in language_findings(text,'English'))


@pytest.mark.parametrize('visible',['A sign reads "你好".', 'The screen displays “开始”.', 'The on-screen text is "第一幕".'])
def test_visible_text_and_original_dialogue_are_not_rejected(visible):
    text=f'detailed_description: [Shot 1] {visible} (S1) says: <d>[Chinese] 准备好了，现在开始。</d>'
    assert not language_findings(text,'English',(('准备好了，现在开始。','Chinese'),))


def test_duplicate_introductions_rejected_but_separate_utterances_allowed():
    text='detailed_description: [Shot 1] <Subject 1> (S1) says, <Subject 1> (S1) says: <d>[Chinese] 你好。</d>'
    assert any(f.rule=='DINGO-duplicate-speech-prefix' for f in language_findings(text,'English'))
    normal='detailed_description: [Shot 1] <Subject 1> (S1) says: <d>[Chinese] 你好。</d> [Shot 2] <Subject 1> (S1) says: <d>[Chinese] 再见。</d>'
    assert not language_findings(normal,'English')


@pytest.mark.parametrize('subject',['He','<Subject 1> (S1)'])
def test_voice_to_say_introduction_from_live_r04_keeps_delivery_once(subject):
    brief,speakers=example()
    text=subject+' uses the clear voice timbre referenced from <Audio 1> to say, {{D1}}'
    out=restore_dialogue(text,brief,speakers)
    assert out=='<Subject 1> (S1), using the clear voice timbre referenced from <Audio 1>, says: <d>[Chinese] 周末一起去看海，我们早点出发。</d>'


@pytest.mark.parametrize('intro',['The woman says, ', 'He uses the warm voice timbre referenced from <Audio 1> to say, '])
def test_unhandled_introduction_is_rejected_instead_of_shipping_a_duplicate(intro):
    text='detailed_description: [Shot 1] '+intro+'<Subject 1> (S1) says: <d>[Chinese] 你好。</d>'
    assert any(f.rule=='DINGO-duplicate-speech-prefix' for f in language_findings(text,'English'))
