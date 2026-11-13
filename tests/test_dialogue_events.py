"""Occurrence-aware speech contracts: legal repeats, negation and cut continuity."""
from dataclasses import replace
from types import SimpleNamespace
import re
import pytest
from h3ir.models import Brief,DialogueLine,Finding
from h3ir.language import prepare,protected_intent,prompt_policy
from h3ir.validate import validate,Context

@pytest.mark.parametrize('intent',[
 '人物先说：“你好。”停顿一秒后再说一次：“你好。”然后闭嘴。',
 '甲说“你好。”乙说“你好。”',
 '人物说“你好。”两次。',
 '人物说两次：“你好。”',
 'She says "你好。" twice.',
])
def test_authorized_repeated_speech_is_two_events(intent):
 b=prepare(Brief(intent=intent));assert [d.text for d in b.dialogue]==['你好。','你好。']
 assert len(prepare(b).dialogue)==2
 assert '{{D1}}' in protected_intent(b)
 assert '{{D1}}, {{D2}}' in prompt_policy('',b)

@pytest.mark.parametrize('intent',[
 '不要让他说“你好”。','别叫她说“你好”。','禁止人物说“你好”。',
 'Do not let him say "hello".','Never say "hello".','Do not say "hello".',
 '人物只微笑点头，不要让他说“你好”，也不要说其它任何话。',
])
def test_negative_quoted_content_is_not_required_speech(intent):
 assert not prepare(Brief(intent=intent)).dialogue

@pytest.mark.parametrize('intent',[
 '不要害怕，直接说“你好”。', '不要加字幕，人物说“你好”。',
 'Do not worry, say "你好".',
])
def test_previous_negative_clause_does_not_negate_positive_speech(intent):
 assert [d.text for d in prepare(Brief(intent=intent)).dialogue]==['你好']

def test_repeated_sources_and_metadata_do_not_double_the_count():
 b=prepare(Brief(intent='甲说“你好。”，乙说“你好。”',dialogue=[DialogueLine('你好。'),DialogueLine('你好。')]))
 assert len(b.dialogue)==2
 assert b.dialogue[0].text==b.dialogue[1].text

def test_repeated_native_speech_keeps_occurrence_identity():
 intent='[Shot 1] (S1) says <d>[Chinese] 你好。</d> Then (S1) says <d>[Chinese] 你好。</d>'
 b=prepare(Brief(intent=intent));assert len(b.dialogue)==2
 t=protected_intent(b);assert t.count('{{D1}}')==1 and t.count('{{D2}}')==1
 assert '你好' not in t

def test_two_speakers_with_same_words_keep_separate_hints():
 b=prepare(Brief(intent='甲说“你好。”乙说“你好。”'))
 assert [d.speaker_hint for d in b.dialogue]==['甲','乙']
 assert '甲说{{D1}}乙说{{D2}}'==protected_intent(b)

def doc(content):return 'integrated_multimodal_description: [Shot 1] Live-action. Static Shot. '+content+'\n\noverall_soundscape: Room tone.\n\nnon_diegetic_music: N/A'

def speech_rules(text,expected):
 return {f.rule for f in validate(text,Context(mode='t2va',n_pictures=0,duration_s=8,expected_dialogue=expected)) if f.severity=='ERROR' and f.rule.startswith('D')}

@pytest.mark.parametrize('actual,expected,rule',[(2,2,None),(1,2,'D4-dialogue-not-verbatim'),(3,2,'D10-dialogue-line-duplicated'),(2,1,'D10-dialogue-line-duplicated')])
def test_occurrence_count_is_checked_in_both_directions(actual,expected,rule):
 text=doc(' A pause. '.join('(S1) says <d>[Chinese] 你好。</d>' for _ in range(actual)))
 rules=speech_rules(text,('你好。',)*expected)
 if rule:assert rule in rules
 else:assert not rules,rules

def test_authorized_repeat_can_still_be_split_across_cuts():
 line='We are ready, let us begin.'
 text=doc('(S1) <d>[English] We are ready, </d> <scenetrans>\n[Shot 2] At 00:02.000, The voice continues seamlessly. <scenetrans> (S1) <d>[English] let us begin.</d> A pause. (S1) <d>[English] We are ready, </d> <scenetrans>\n[Shot 3] At 00:04.000, The voice continues seamlessly. <scenetrans> (S1) <d>[English] let us begin.</d>')
 assert not speech_rules(text,(line,line))
 assert 'D11-split-line-no-scenetrans' in speech_rules(text.replace('<scenetrans>',''),(line,line))

def test_legal_repetition_is_not_mechanically_rewritten_as_an_echo():
 from h3ir.repair import _fix_echoed_dialogue,RepairResult
 line='We are ready, let us begin right away.'
 text=doc('<d>[English] '+line+'</d> [Shot 2] <d>[English] We are ready, </d> <scenetrans> [Shot 3] <scenetrans> <d>[English] let us begin right away.</d>')
 assert _fix_echoed_dialogue(text,(line,line),RepairResult(text))==text

def test_correction_preserves_two_equal_events_and_protects_their_words():
 from h3ir.prose import fix_with_findings
 class Backend:
  messages=None
  def chat(self,messages,**kw):self.messages=messages;return SimpleNamespace(content='{{UTTERANCE1}} A one-second pause. {{UTTERANCE2}}')
 b=prepare(Brief(intent='甲说“你好。”，停顿一秒后再说“你好。”'));backend=Backend()
 out=fix_with_findings(backend,doc('<d>[Chinese] 你好。</d> A pause. <d>[Chinese] 你好。</d>'),[],brief=b,review=True)
 assert re.findall(r'<d>\[Chinese\] (.*?)</d>',out)==['你好。','你好。']
 ask=str(backend.messages);assert '你好' not in ask
 source=backend.messages[-1]['content'].split('YOUR TEXT\n',1)[1]
 assert source.count('{{UTTERANCE1}}')==source.count('{{UTTERANCE2}}')==1
 assert 'different tokens must not be deduplicated' in ask

def test_no_speech_is_explicit_and_does_not_suppress_room_tone_or_music():
 from h3ir.compile import _scope
 from h3ir.creativity import SPEECH,SCORE
 plan=SimpleNamespace(label_counts=lambda:{'Audio':0},manifest=[])
 b=prepare(Brief(intent='生成静默视频，不要让他说“你好”。',creativity='bold'))
 scope=_scope(b,plan);assert SPEECH in scope.forbidden and SCORE not in scope.forbidden
 assert 'Q1-forbidden-element-present' in {f.rule for f in validate(doc('<d>[English] Hello.</d>'),Context(mode='t2va',n_pictures=0,duration_s=8,scope=scope))}
 b=prepare(Brief(intent='安静的办公室，人物微笑。',creativity='bold'))
 assert SPEECH not in _scope(b,plan).forbidden


def test_repeated_metadata_also_protects_unquoted_literal_mentions():
 b=prepare(Brief(intent='He says Hello. and then Hello.',dialogue=[DialogueLine('Hello.'),DialogueLine('Hello.')]))
 t=protected_intent(b)
 assert 'Hello.' not in t and '{{D1}}' in t and '{{D2}}' in t


def test_correction_keeps_parts_of_two_equal_cross_cut_utterances_distinct():
 from h3ir.prose import fix_with_findings
 line='We are ready, let us begin.'
 class Backend:
  messages=None
  def chat(self,messages,**kw):self.messages=messages;return SimpleNamespace(content='{{UTTERANCE1_PART1}} {{UTTERANCE1_PART2}} {{UTTERANCE2_PART1}} {{UTTERANCE2_PART2}}')
 b=Brief(intent='Say the line twice.',dialogue=[DialogueLine(line,'English'),DialogueLine(line,'English')]);backend=Backend()
 part='<d>[English] We are ready, </d> <scenetrans> [Shot 2] <scenetrans> <d>[English] let us begin.</d>'
 fix_with_findings(backend,doc(part+' A pause. '+part),[],brief=b,review=True)
 source=backend.messages[-1]['content'].split('YOUR TEXT\n',1)[1]
 assert source.count('{{UTTERANCE1_PART1}}')==source.count('{{UTTERANCE1_PART2}}')==1
 assert source.count('{{UTTERANCE2_PART1}}')==source.count('{{UTTERANCE2_PART2}}')==1


def test_correction_findings_reference_registered_speech_tokens():
 from h3ir.prose import fix_with_findings
 line='周末一起去看海，我们早点出发。'
 class Backend:
  messages=None
  def chat(self,messages,**kw):self.messages=messages;return SimpleNamespace(content='{{UTTERANCE1}}')
 backend=Backend();b=Brief(intent='甲说这句台词。',dialogue=[DialogueLine(line,'Chinese')])
 fix_with_findings(backend,doc('<d>[Chinese] '+line+'</d>'),[Finding('D11-split-line-no-scenetrans','ERROR','the line '+line+' requires two connecting markers')],brief=b)
 findings=backend.messages[-1]['content'].split('FINDINGS\n',1)[1].split('YOUR TEXT\n',1)[0]
 assert '{{UTTERANCE1}}' in findings and '{{D1}}' not in findings
 assert line not in str(backend.messages)


def test_summary_mentions_do_not_schedule_extra_protected_speech():
 from h3ir.language import restore_dialogue
 b=Brief(intent='Say Hello twice.',dialogue=[DialogueLine('你好。','Chinese'),DialogueLine('你好。','Chinese')])
 text='summary: He delivers {{D1}} and {{D2}}, with a pause.\n\ndetailed_description: [Shot 1] He says {{D1}}. A one-second pause. He says {{D2}}.\n\noverall_soundscape: Room tone.\n\nnon_diegetic_music: N/A'
 out=restore_dialogue(text,b)
 assert out.startswith('summary: He delivers a supplied utterance and a supplied utterance, with a pause.')
 assert '<d>' not in out.split('detailed_description:')[0]
 assert re.findall(r'<d>\[Chinese\] (.*?)</d>',out)==['你好。','你好。']
 assert 'one-second pause' in out and out.endswith('non_diegetic_music: N/A')


def test_non_shot_unknown_speech_is_not_silently_discarded():
 from h3ir.language import restore_dialogue
 b=Brief(intent='Say Hello.',dialogue=[DialogueLine('Hello.','English')])
 text='summary: He says <d>[English] Buy now.</d>\n\ndetailed_description: [Shot 1] He says {{D1}}.'
 out=restore_dialogue(text,b)
 assert '<d>[English] Buy now.</d>' in out and '<d>[English] Hello.</d>' in out


@pytest.mark.parametrize('intent,hint',[
 ('图1照视频1的样子转头看图2，用音频1那样的声音说：“先看这里，拿稳一下。”','图1'),
 ('图1拿着鼠标给图2看看，说：“这个手感，办公正合适。”','图1'),
 ('甲用右手轻握鼠标，向乙提起少许，并用普通话说：“这个手感，办公正合适。”','甲'),
])
def test_listener_reference_does_not_replace_the_speaking_subject(intent,hint):
 b=prepare(Brief(intent=intent))
 assert len(b.dialogue)==1 and b.dialogue[0].speaker_hint==hint
