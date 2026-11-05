from types import SimpleNamespace
import pytest
from h3ir.models import Brief, DialogueLine, Finding, Mode
from h3ir.prose import fix_with_findings, correction_spec
from h3ir.language import prompt_policy
from h3ir.config import get_config
LINE='周末一起去看海，我们早点出发。'

class Capture:
 def __init__(self, output='{{UTTERANCE1}}'):
  self.output=output;self.calls=[]
 def chat(self,messages,**kwargs):
  self.calls.append((messages,kwargs));return SimpleNamespace(content=self.output)

@pytest.mark.parametrize('ref',[False,True])
def test_independent_correction_carries_mode_rules_and_protected_original_request(ref):
 b=Capture();brief=Brief(intent='她说：“'+LINE+'”',dialogue=[DialogueLine(LINE,language='Chinese')]);sections=('subject_definitions','summary','retention_analysis','detailed_description','overall_soundscape','non_diegetic_music') if ref else ('integrated_multimodal_description','overall_soundscape','non_diegetic_music')
 fix_with_findings(b,'(S1) says: <d>[Chinese] '+LINE+'</d>',[Finding('X','ERROR','fix prefix')],sections=sections,brief=brief,speaker_bindings=(('(S1)','<Subject 1>','<Audio 1>'),))
 messages,_=b.calls[0];system=messages[0]['content'];ask=messages[1]['content']
 assert '=== SPECIFICATION ===' not in system  # Extracted content, not creative writer instructions.
 assert 'Video Prompt Writing Guide' in system
 assert ('Full-Reference Mode Rewrite Output Format Guide' in system)==ref
 assert 'H3 dialogue continuity and speaker structure rules (v3)' in system
 assert 'ORIGINAL REQUIREMENTS' in ask and 'SPEAKER BINDINGS' in ask
 assert '<Audio 1>' in ask and LINE not in str(messages)
 assert '她说：{{UTTERANCE1}}' in ask


def test_initial_and_correction_share_dialogue_rule_version():
 brief=Brief(intent='test')
 assert 'H3 dialogue continuity and speaker structure rules (v3)' in prompt_policy('compose',brief)
 b=Capture();fix_with_findings(b,'text',[],brief=brief)
 assert 'H3 dialogue continuity and speaker structure rules (v3)' in b.calls[0][0][0]['content']


def test_review_protects_split_dialogue_and_records_explicit_voice_binding():
 b=Capture('{{UTTERANCE1_PART1}} <scenetrans> [Shot 2] <scenetrans> {{UTTERANCE1_PART2}}');brief=Brief(intent='甲用音频2说：“'+LINE+'”',dialogue=[DialogueLine(LINE,language='Chinese',speaker_hint='甲')])
 out=fix_with_findings(b,'(S1) says: <d>[Chinese] '+LINE+'</d>',[],brief=brief,review=True,speaker_bindings=(('(S1)',None,'<Audio 2>'),))
 assert '<d>[Chinese] 周末一起去看海，</d>' in out and '<d>[Chinese] 我们早点出发。</d>' in out
 assert LINE not in str(b.calls[0][0]) and 'voice=<Audio 2>' in str(b.calls[0][0])
 assert b.calls[0][1]['temperature']==0.0
 assert 'draft may have passed mechanical checks' in str(b.calls[0][0])


class Pipeline:
 cfg=get_config().llm
 def __init__(self, extra=False,drop=False):self.calls=[];self.extra=extra;self.drop=drop
 def require_available(self):pass
 def server_version(self):return 'test'
 def chat(self,messages,**kwargs):
  self.calls.append(messages)
  if len(self.calls)==1:
   return SimpleNamespace(content='integrated_multimodal_description: [Shot 1] A realistic scene. Static Shot. She says: {{D1}}\noverall_soundscape: Quiet room tone.\nnon_diegetic_music: N/A')
  if self.drop:
   return SimpleNamespace(content='integrated_multimodal_description: [Shot 1] A realistic scene. Static Shot. She is silent.\noverall_soundscape: Quiet room tone.\nnon_diegetic_music: N/A')
  out=messages[-1]['content'].split('YOUR TEXT\n',1)[1]
  if self.extra:out=out.replace('overall_soundscape:', '<d>[English] Added words.</d>\noverall_soundscape:')
  return SimpleNamespace(content=out)


def compile_fake(monkeypatch, backend, dialogue=True):
 import h3ir.compile as c
 monkeypatch.setattr(c,'analyse_all',lambda *a,**k:{})
 brief=Brief(intent='She speaks once.' if dialogue else 'A quiet empty room.',mode=Mode.T2VA,dialogue=[DialogueLine(LINE,language='Chinese')] if dialogue else [])
 return c.compile_brief(brief,backend=backend)


def test_clean_first_draft_is_still_reviewed_and_result_revalidated(monkeypatch):
 b=Pipeline();doc=compile_fake(monkeypatch,b)
 assert doc.source=='written' and len(b.calls)==2
 assert doc.provenance['dialogue_review']['performed'] is True
 assert doc.provenance['dialogue_review']['policy']=='dialogue_review.v3'
 assert 'dialogue_review_s' in doc.provenance['timings']
 assert doc.prompt.count(LINE)==1
 assert all(LINE not in str(x) for x in b.calls)


def test_review_added_speech_cannot_ship_as_ready(monkeypatch):
 b=Pipeline(extra=True);doc=compile_fake(monkeypatch,b)
 assert doc.source=='draft' and 'DINGO-speech-inventory' in doc.fallback_reason
 assert doc.provenance['dialogue_review']['performed'] is True
 assert len(b.calls)<=4  # compose + review + bounded corrections


def test_review_lost_speech_cannot_ship_as_ready(monkeypatch):
 b=Pipeline(drop=True);doc=compile_fake(monkeypatch,b)
 assert doc.source=='draft' and 'D4-dialogue-not-verbatim' in doc.fallback_reason
 assert '<d>[Chinese] '+LINE+'</d>' in doc.prompt


@pytest.mark.parametrize('prefix',['delivers the line','delivering the line','finishing the sentence','delivering'])
def test_compound_speech_intro_from_reported_r04_is_rejected(prefix):
 from h3ir.language import language_findings
 text='detailed_description: [Shot 1] <Subject 1> (S1) begins to speak, '+prefix+' <Subject 1> (S1), using the voice timbre referenced from <Audio 1>, says: <d>[Chinese] 你好。</d>'
 assert any(f.rule=='DINGO-duplicate-speech-prefix' for f in language_findings(text,'English'))


def test_listener_action_followed_by_speaker_is_not_duplicate_intro():
 from h3ir.language import language_findings
 text='detailed_description: [Shot 1] The listener smiles as <Subject 1> (S1), using the voice timbre referenced from <Audio 1>, says: <d>[Chinese] 你好。</d>'
 assert not language_findings(text,'English')


def test_semantic_review_can_repair_unlisted_schedule_and_unpinned_shots():
 b=Capture();brief=Brief(intent='Cut while she speaks.',dialogue=[DialogueLine(LINE,language='Chinese')])
 fix_with_findings(b,'[Shot 1] <d>[Chinese] '+LINE+'</d> [Shot 2] She continues speaking.',[],brief=brief,review=True)
 messages=b.calls[0][0];s=str(messages)
 assert 'Resolve speech scheduling contradictions even when FINDINGS is empty' in s
 assert 'may change draft-invented shot count' in s
 assert 'A whole utterance token consumes the entire line' in s
 assert 'Fix every finding and change nothing else' not in s
 assert 'You correct mechanical faults in an H3 brief' not in s


def test_mechanical_fix_retains_cross_cut_split_choices():
 b=Capture();brief=Brief(intent='Cut mid sentence.',dialogue=[DialogueLine(LINE,language='Chinese')])
 fix_with_findings(b,'<d>[Chinese] '+LINE+'</d>',[Finding('P5','ERROR','camera')],brief=brief)
 assert '{{UTTERANCE1_PART1}}' in str(b.calls[0][0])


def test_final_semantic_review_after_mechanical_fix_is_revalidated(monkeypatch):
 import h3ir.compile as c
 original=c.validate;calls=[]
 def validate_once(*a,**k):
  calls.append(1);found=original(*a,**k)
  return found+[Finding('test-camera','ERROR','Fix camera')] if len(calls)==2 else found
 monkeypatch.setattr(c,'validate',validate_once)
 b=Pipeline();doc=compile_fake(monkeypatch,b)
 assert doc.source=='written' and len(b.calls)==4
 assert doc.provenance['dialogue_review']['final_performed'] is True
 assert len(calls)==4
 assert 'Review this H3 draft' in b.calls[-1][-1]['content']


def test_already_split_draft_keeps_canonical_original_line_identity():
 brief=Brief(intent='她说：“'+LINE+'”',dialogue=[DialogueLine(LINE,language='Chinese')])
 b=Capture('{{UTTERANCE1_PART1}} <scenetrans> [Shot 2] <scenetrans> {{UTTERANCE1_PART2}}')
 out=fix_with_findings(b,'[Shot 1] <d>[Chinese] 周末一起去看海，</d> [Shot 2] <d>[Chinese] 我们早点出发。</d>',[],brief=brief,review=True)
 ask=b.calls[0][0][1]['content']
 assert '她说：{{UTTERANCE1}}' in ask and '{{D1}}' not in ask
 assert '{{UTTERANCE1}} is ONE caller-supplied line' in ask
 draft=ask.split('YOUR TEXT\n')[1]
 assert '{{UTTERANCE1_PART1}}' in draft and '{{UTTERANCE1_PART2}}' in draft
 assert '周末一起去看海，</d>' in out and '我们早点出发。</d>' in out
 assert out.count('我们早点出发。')==1 and LINE not in out


def test_untagged_english_dialogue_keeps_english_language():
 brief=Brief(intent='She says hello.',dialogue=[DialogueLine('Hello, ready.')])
 b=Capture('{{UTTERANCE1}}')
 out=fix_with_findings(b,'<d>[English] Hello, ready.</d>',[],brief=brief,review=True)
 assert '<d>[English] Hello, ready.</d>' in out and '[Chinese]' not in out


def test_review_carries_cross_cut_boundary_placement_rule():
 b=Capture();brief=Brief(intent='Cut mid-sentence.',dialogue=[DialogueLine(LINE,language='Chinese')])
 fix_with_findings(b,'<d>[Chinese] '+LINE+'</d> <scenetrans>. The audio continues uninterrupted across the cut.',[],brief=brief,review=True)
 system=b.calls[0][0][0]['content'];ask=b.calls[0][0][1]['content']
 assert 'Do not append an English continuity explanation after that closing marker' in system
 assert 'BEFORE its connecting <scenetrans>' in system
 assert 'never after the first shot closing <scenetrans>' in ask
