from types import SimpleNamespace
import pytest
from starlette.testclient import TestClient
from h3ir.structured_input import parse, constraints
from h3ir.models import Brief, Mode, DialogueLine
from h3ir.language import prepare, protected_intent
from h3ir.config import get_config

ONE = "integrated_multimodal_description: [Shot 1] Live-action. Static Shot. A ball falls to the floor.\noverall_soundscape: Quiet room. No dialogue.\nnon_diegetic_music: N/A"
TWO = ONE.replace("A ball falls to the floor.", "A ball falls. [Shot 2] At 00:04.000, The shot cuts to the ball at rest.")

@pytest.mark.parametrize('text,count',[(ONE,1),(TWO,2)])
def test_reliable_headers_and_explicit_cut_constraints(text,count):
 p=parse(text)
 assert p.reliable and len(p.shots)==count
 assert '[Shot' not in p.shots[0]['body']
 changed=text.replace('[Shot 2] At 00:04.000,','[Shot 2] At 00:05.000,') if count==2 else TWO
 assert constraints(p,changed)[0].rule=='DINGO-input-shot-structure'

@pytest.mark.parametrize('text',[
 '普通中文，拍一个小球落地。','{"shots":[{"action":"drop"}]}',
 '```text\n'+ONE+'\n```',ONE.replace('description:', 'description：'),
 TWO.replace('[Shot 2]','[Shot 1]'),ONE+'\nunknown_field: preserve this',
 ONE.replace('A ball falls to the floor.','She says <d>[Chinese] 你好。'),
])
def test_uncertain_formats_are_understanding_not_rejection(text):
 p=parse(text)
 assert not p.reliable and p.original==text and p.evidence()['route']=='needs_understanding'


def test_literal_shot_in_speech_or_lettering_is_not_a_header():
 p=parse(ONE.replace('A ball falls to the floor.', 'A sign reads "[Shot 9]". She says <d>[English] [Shot 8]</d>'))
 assert p.reliable and len(p.shots)==1
 assert '[Shot 9]' in p.shots[0]['body'] and '[Shot 8]' in p.shots[0]['body']


def test_native_dialogue_is_protected_and_explicit_metadata_not_duplicated():
 text=ONE.replace('A ball falls to the floor.', '(S1) says: <d>[Chinese] 准备好了，现在开始。</d>')
 b=prepare(Brief(intent=text))
 assert [(d.text,d.language) for d in b.dialogue]==[('准备好了，现在开始。','Chinese')]
 assert '准备好了' not in protected_intent(b)
 b=prepare(Brief(intent=text,dialogue=[DialogueLine('准备好了，现在开始。',language='zh')]))
 assert len(b.dialogue)==1


class Writer:
 cfg=get_config().llm
 def __init__(self,output=ONE):self.output=output;self.calls=[]
 def require_available(self):pass
 def server_version(self):return 'fixture'
 def chat(self,messages,**kwargs):
  self.calls.append(messages)
  return SimpleNamespace(content=self.output)

@pytest.mark.parametrize('original',[ONE,'```text\n'+ONE+'\n```',TWO.replace('[Shot 2]','[Shot 1]')])
def test_actual_compile_reaches_writer_and_never_ships_nested_document(monkeypatch,original):
 import h3ir.compile as c
 monkeypatch.setattr(c,'analyse_all',lambda *a,**kw:{})
 backend=Writer()
 doc=c.compile_brief(Brief(intent=original,mode=Mode.T2VA),backend=backend)
 assert backend.calls and doc.source=='written' and doc.ok
 assert doc.prompt.count('[Shot 1]')==1
 assert doc.provenance['input_normalization']['original_sha256']==parse(original).evidence()['original_sha256']
 assert 'INPUT UNDERSTANDING' in str(backend.calls[0])


def test_invalid_provisional_draft_cannot_be_a_successful_fallback(monkeypatch):
 import h3ir.compile as c
 monkeypatch.setattr(c,'analyse_all',lambda *a,**kw:{})
 with pytest.raises(c.CompilerInvariantError):
  c.compile_brief(Brief(intent='```\n'+ONE+'\n```',mode=Mode.T2VA),backend=Writer('unusable'))


def test_http_invariant_is_structured_and_does_not_leak_paths(monkeypatch):
 from h3ir import service
 from h3ir.compile import CompilerInvariantError
 def broken(*a,**kw):raise CompilerInvariantError('/private/source internals')
 monkeypatch.setattr(service,'compile_brief',broken)
 response=TestClient(service.app,raise_server_exceptions=False).post('/v1/briefs',json={'intent':'ball drops'})
 assert response.status_code==500
 assert response.json()['detail']['code']=='compiler-invariant'
 assert len(response.json()['detail']['trace_id'])==32
 assert '/private' not in response.text


@pytest.mark.parametrize('actor',['a hand slowly tilts the cup','the knife moves down','the wooden block moves forward'])
def test_static_camera_and_physical_subject_motion_are_not_contradictory(actor):
 from tests.test_hardening import _rules, _t2va
 desc='[Shot 1] Live-action. The camera holds a static shot as '+actor+'.'
 assert 'R20-camera-contradiction' not in _rules(_t2va(desc),mode='t2va',duration_s=8.0)


def test_actual_static_camera_movement_is_still_rejected():
 from tests.test_hardening import _rules, _t2va
 desc='[Shot 1] Live-action. The camera holds a static shot as the camera slowly moves forward.'
 assert 'R20-camera-contradiction' in _rules(_t2va(desc),mode='t2va',duration_s=8.0)


def test_subject_amplitude_determiner_is_not_camera_modifier():
 from tests.test_hardening import _rules, _t2va
 desc='[Shot 1] Live-action. Static Shot. The pendulum swings, with the amplitude gradually decreasing.'
 assert 'P8-camera-modifier-off-vocabulary' not in _rules(_t2va(desc),mode='t2va',duration_s=8.0)


@pytest.mark.parametrize('name',['OK','peace'])
def test_named_gesture_is_not_invented_lettering(name):
 from h3ir.validate import validate
 from tests.test_closed_vocabulary import _ctx, _doc
 from dataclasses import replace
 text=_doc('[Shot 1] Live-action. Static Shot. The hand forms the "'+name+'" gesture.')
 from h3ir.compile import _scope
 from h3ir.plan import build_plan
 b=Brief(intent='Animate a hand gesture.')
 ctx=replace(_ctx(),scope=_scope(b,build_plan(b,Mode.T2VA,{})))
 assert not any(f.rule in ('Q1-forbidden-element-present','Q2-unlicensed-addition') for f in validate(text,ctx))
 visible=text.replace('The hand forms the', 'A label displays the word')
 assert any(f.rule=='Q2-unlicensed-addition' for f in validate(visible,ctx))
