"""A misplaced incoming marker is advisory only after the other checks pass."""
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
import json
import pytest
from h3ir.models import Brief, DialogueLine, Finding, SpeakerPlan, Mode
from h3ir.validate import validate, Context
from h3ir.dialogue_format import classify_late_scenetrans, D11

DATA=json.loads((Path(__file__).parent/'fixtures/r04_late_scenetrans.json').read_text())

def example():
 return DATA['prompt'],Brief(intent='Two supplied lines.',dialogue=[DialogueLine(**d) for d in DATA['dialogue']]),[SpeakerPlan(**s) for s in DATA['speakers']]

def checks(text,brief):
 return validate(text,Context(mode='ref2va',n_pictures=3,n_audios=2,description_language='English',expected_dialogue=tuple(d.text for d in brief.dialogue)))

def error():return Finding(D11,'ERROR','both connecting points must be marked')

def test_recorded_candidate_is_warned_without_mutating_text_or_original_findings():
 text,brief,speakers=example();original=text;found=checks(text,brief)
 assert [f.rule for f in found if f.severity=='ERROR']==[D11]
 out=classify_late_scenetrans(text,brief,speakers,found)
 assert text==original and next(f for f in found if f.rule==D11).severity=='ERROR'
 assert not [f for f in out if f.severity=='ERROR']
 warning=next(f for f in out if f.rule==D11)
 assert warning.severity=='WARN' and 'retained unchanged' in warning.msg
 assert [f for f in out if f.rule!=D11]==[f for f in found if f.rule!=D11]

@pytest.mark.parametrize('rule',['D4-dialogue-not-verbatim','D10-dialogue-line-duplicated','DINGO-speech-inventory','DINGO-voice-binding','S1-missing-section','another-future-hard-rule'])
def test_any_other_hard_error_prevents_reclassification(rule):
 text,brief,speakers=example();found=[error(),Finding(rule,'ERROR','must still block')]
 assert classify_late_scenetrans(text,brief,speakers,found)==found

@pytest.mark.parametrize('change',[
 'missing-first','missing-second','late-after-prose','quoted-late','no-cut','unrelated-cut','no-speaker','switched-speaker','switched-subject','wrong-voice','missing-voice-proof','extra-spoken-words','omitted-line','reversed-lines','different-language',
])
def test_ambiguous_or_incomplete_evidence_stays_hard(change):
 text,brief,speakers=example();first='</d> <scenetrans>\n[Shot 2]';late='<d>[Chinese] 我们早点出发。</d> <scenetrans>'
 if change=='missing-first':text=text.replace(first,'</d>\n[Shot 2]',1)
 elif change=='missing-second':text=text.replace(late,'<d>[Chinese] 我们早点出发。</d>',1)
 elif change=='late-after-prose':text=text.replace(late,'<d>[Chinese] 我们早点出发。</d> He pauses. <scenetrans>',1)
 elif change=='quoted-late':text=text.replace(late,'<d>[Chinese] 我们早点出发。</d> "<scenetrans>"',1)
 elif change=='no-cut':text=text.replace('[Shot 2] At 00:04.000,','',1)
 elif change=='unrelated-cut':text=text.replace(late,'<d>[Chinese] 我们早点出发。</d> <scenetrans> [Shot 3] At 00:05.000,',1)
 elif change=='no-speaker':text=text.replace('<Subject 1> (S1) continues seamlessly across the cut:','The voice continues:',1)
 elif change=='switched-speaker':text=text.replace('<Subject 1> (S1) continues seamlessly across the cut:','<Subject 2> (S2) continues seamlessly across the cut:',1)
 elif change=='switched-subject':text=text.replace('<Subject 1> (S1) continues seamlessly across the cut:','<Subject 2> (S1) continues seamlessly across the cut:',1)
 elif change=='wrong-voice':text=text.replace('<Subject 1> (S1) continues seamlessly across the cut:','<Subject 1> (S1), using <Audio 2>, continues:',1)
 elif change=='missing-voice-proof':text=text.replace(', using the voice timbre referenced from <Audio 1>,','',1)
 elif change=='extra-spoken-words':text=text.replace('我们早点出发。</d>','我们早点出发。多说一句。</d>',1)
 elif change=='omitted-line':text=text.replace('好，我来准备。','',1)
 elif change=='reversed-lines':text=text.replace('周末一起去看海，','好，我来准备。',1).replace('好，我来准备。</d> He finishes','周末一起去看海，</d> He finishes',1)
 elif change=='different-language':text=text.replace('[Chinese] 我们早点出发。','[English] 我们早点出发。',1)
 assert text!=DATA['prompt'],change
 found=[error()]  # The classifier must itself require evidence, even without another finding.
 assert classify_late_scenetrans(text,brief,speakers,found)==found,change


def test_unknown_voice_binding_does_not_become_a_default():
 text,brief,speakers=example();found=[error(),Finding('DINGO-voice-binding-unresolved','WARN','unresolved')]
 assert classify_late_scenetrans(text,brief,speakers,found)==found


def test_extra_speech_outside_the_shots_cannot_balance_the_inventory():
 text,brief,speakers=example();text=text.replace('summary:\n','summary:\n<d>[Chinese] 好，我来准备。</d> ',1)
 assert classify_late_scenetrans(text,brief,speakers,[error()])==[error()]


def test_extra_complete_line_is_not_authorized_by_a_late_marker():
 text,brief,speakers=example();text=text.replace('overall_soundscape:', '(S2) says: <d>[Chinese] 好，我来准备。</d>\n\noverall_soundscape:',1)
 assert classify_late_scenetrans(text,brief,speakers,[error()])==[error()]


def test_correct_boundaries_and_missing_markers_keep_original_validator_behavior():
 text,brief,speakers=example()
 correct=text.replace('continues seamlessly across the cut: <d>', 'continues seamlessly across the cut: <scenetrans> <d>',1).replace('我们早点出发。</d> <scenetrans>','我们早点出发。</d>',1)
 found=checks(correct,brief);assert not [f for f in found if f.rule==D11]
 assert classify_late_scenetrans(correct,brief,speakers,found)==found
 for missing in [text.replace('<scenetrans>',''),text.replace('我们早点出发。</d> <scenetrans>','我们早点出发。</d>')]:
  found=checks(missing,brief);assert any(f.rule==D11 and f.severity=='ERROR' for f in found)
  assert classify_late_scenetrans(missing,brief,speakers,found)==found


def test_three_part_splits_remain_strict_when_the_marker_may_belong_to_an_outgoing_join():
 text='integrated_multimodal_description: [Shot 1] Static Shot. (S1) <d>[English] One, </d> <scenetrans>\n[Shot 2] At 00:01.000, (S1) <d>[English] two, </d> <scenetrans>\n[Shot 3] At 00:02.000, (S1) <d>[English] three.</d> <scenetrans>\noverall_soundscape: Room tone.\nnon_diegetic_music: N/A'
 brief=Brief(intent='Three parts.',dialogue=[DialogueLine('One, two, three.','English')]);speakers=[SpeakerPlan('(S1)',None)]
 assert classify_late_scenetrans(text,brief,speakers,[error(),error()])==[error(),error()]


def test_reversed_complete_utterances_do_not_pass_by_character_inventory():
 text,brief,speakers=example();brief=replace(brief,dialogue=list(reversed(brief.dialogue)))
 assert classify_late_scenetrans(text,brief,speakers,[error()])==[error()]


class Pipeline:
 def __init__(self,text):
  from h3ir.config import get_config
  self.cfg=get_config().llm;self.calls=[];self.text=text
 def require_available(self):pass
 def server_version(self):return 'local-regression'
 def chat(self,messages,**kwargs):self.calls.append(messages);return SimpleNamespace(content=self.text)


def test_compile_returns_written_original_after_existing_correction_budget(monkeypatch):
 import h3ir.compile as c
 from h3ir.config import get_config
 cfg=replace(get_config(),requirements_review=False,professional_repair=False);monkeypatch.setattr(c,'get_config',lambda:cfg);monkeypatch.setattr(c,'analyse_all',lambda *a,**kw:{})
 text=PIPELINE_TEXT
 b=Pipeline(text);brief=Brief(intent='A person says the supplied line.',mode=Mode.T2VA,dialogue=[DialogueLine('Ready, let us begin.','English')]);doc=c.compile_brief(brief,backend=b)
 assert doc.source=='written' and doc.ok and doc.prompt==text and doc.fallback_reason is None
 assert len(b.calls)==4 and doc.provenance['fix_rounds']==2
 assert any(f.rule==D11 and f.severity=='WARN' for f in doc.findings)


PIPELINE_TEXT='integrated_multimodal_description: [Shot 1] A realistic, quiet room. Static Shot. (S1) says: <d>[English] Ready, </d> <scenetrans>\n[Shot 2] At 00:02.000, the shot cuts to the listener. The same voice continues seamlessly. (S1) continues: <d>[English] let us begin.</d> <scenetrans> The speaker closes their mouth.\noverall_soundscape: Quiet room tone.\nnon_diegetic_music: N/A\n'

@pytest.mark.parametrize('passes',[True,False])
def test_optional_requirements_review_cannot_be_bypassed(monkeypatch,passes):
 import h3ir.compile as c
 import h3ir.requirements as requirements
 from h3ir.config import get_config
 cfg=replace(get_config(),requirements_review=True,professional_repair=False);monkeypatch.setattr(c,'get_config',lambda:cfg);monkeypatch.setattr(c,'analyse_all',lambda *a,**kw:{})
 monkeypatch.setattr(requirements,'extract',lambda *a,**kw:{})
 monkeypatch.setattr(requirements,'apply',lambda brief,ledger:brief)
 audited=[]
 def review(*args,**kwargs):
  audited.append(args[2])
  return {'issues':[] if passes else [{'kind':'uncertain','explanation':'Must remain blocking'}]}
 monkeypatch.setattr(requirements,'review',review)
 brief=Brief(intent='A person says the supplied line.',mode=Mode.T2VA,dialogue=[DialogueLine('Ready, let us begin.','English')]);backend=Pipeline(PIPELINE_TEXT)
 if passes:
  doc=c.compile_brief(brief,backend=backend)
  assert doc.source=='written' and doc.provenance['requirements_review'].get('passed') is True
 else:
  with pytest.raises(requirements.RequirementsReviewFailed):c.compile_brief(brief,backend=backend)
 assert len(audited)==1 and audited[0]==PIPELINE_TEXT
