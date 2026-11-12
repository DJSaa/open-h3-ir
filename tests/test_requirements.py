import copy
from dataclasses import replace
import pytest
from h3ir import requirements as R
from h3ir.models import Brief, DialogueLine
from h3ir.backend import BackendError
from h3ir.style import style_terms_in, resolve_style
from tests.test_structured_input import ONE, Writer

class Extractor:
 def __init__(self,obj):self.obj=obj;self.calls=[]
 def json_call(self,messages,*a,**kw):self.calls.append(messages);return copy.deepcopy(self.obj)

def ledger(text='No music. Cartoon.'):
 return {'requirements':[{'requirement':'Keep the requested scene','source_quote':text}],
         'music':{'policy':'forbidden','source_quote':'No music.'},
         'style':{'description':'2D cartoon animation','source_quote':'Cartoon.'}}

def test_evidence_is_verbatim_and_cannot_be_invented():
 b=Brief(intent='No music. Cartoon.')
 assert R.extract(Extractor(ledger()),b)['music']['policy']=='forbidden'
 bad=ledger();bad['requirements'][0]['source_quote']='invented'
 with pytest.raises(BackendError,match='verbatim'):R.extract(Extractor(bad),b)


def test_structured_na_overrides_unspecified_extraction_and_is_checked():
 obj={'requirements':[{'requirement':'A ball falls','source_quote':'A ball falls to the floor.'}],
      'music':{'policy':'unspecified','source_quote':''},'style':{'description':'','source_quote':''}}
 b=R.apply(Brief(intent=ONE),R.extract(Extractor(obj),Brief(intent=ONE)))
 assert b.silent and not R.mechanical(b,ONE)
 assert R.mechanical(b,ONE.replace('N/A','A piano score.'))


def test_chinese_style_from_semantic_evidence_not_default_live_action():
 text='卡通画面，不要音乐。';obj={'requirements':[{'requirement':'cartoon','source_quote':'卡通画面'}],
 'music':{'policy':'forbidden','source_quote':'不要音乐'},'style':{'description':'2D cartoon animation','source_quote':'卡通画面'}}
 b=R.apply(Brief(intent=text),R.extract(Extractor(obj),Brief(intent=text)))
 assert resolve_style(b,{}).phrase=='2D cartoon animation'
 assert 'inked' not in style_terms_in('The shadow stays linked to the cube.')
 assert 'inked' in style_terms_in('An inked illustration.')


def test_reviewer_gets_original_even_if_ledger_omitted_requirement():
 b=Brief(intent='Under water, release the spoon. No music.',requirements_context={'requirements':[]})
 obj={'issues':[{'kind':'contradiction','original_quote':'Under water','candidate_quote':'above water','explanation':'Release underwater, not above.'}]}
 backend=Extractor(obj);result=R.review(backend,b,'The spoon is released above water.')
 assert R.findings(result)
 assert 'Under water' in str(backend.calls) and 'original' in str(backend.calls)
 obj['issues'][0]['candidate_quote']='invented candidate'
 with pytest.raises(BackendError,match='verbatim'):R.review(Extractor(obj),b,'above water')


def test_speech_literal_does_not_enter_reviewer():
 from h3ir.language import prepare
 b=prepare(Brief(intent='她说：“你好。”',dialogue=[DialogueLine('你好。',language='Chinese')]))
 backend=Extractor({'issues':[]});R.review(backend,b,'She says <d>[Chinese] 你好。</d>')
 assert '你好' not in str(backend.calls)
 assert '{{D1}}' in str(backend.calls)

class Pipeline(Writer):
 def __init__(self,reject=False):super().__init__(ONE);self.reject=reject;self.json_calls=[]
 def json_call(self,messages,*a,**kw):
  self.json_calls.append(messages)
  if len(self.json_calls)==1:
   return {'requirements':[{'requirement':'Keep the ball drop','source_quote':'A ball falls to the floor.'}],
           'music':{'policy':'forbidden','source_quote':'N/A'},'style':{'description':'Live-action','source_quote':'Live-action'}}
  return {'issues':[{'kind':'contradiction','original_quote':'A ball falls to the floor.','candidate_quote':'A ball falls to the floor.','explanation':'Fixture persistent rejection.'}] if self.reject else []}


def test_pipeline_records_gate_and_preserves_default_call_budget(monkeypatch):
 import h3ir.compile as C
 from h3ir.config import get_config
 monkeypatch.setattr(C,'get_config',lambda:replace(get_config(),requirements_review=True))
 monkeypatch.setattr(C,'analyse_all',lambda *a,**k:{})
 b=Pipeline();doc=C.compile_brief(Brief(intent=ONE),backend=b)
 assert doc.source=='written' and doc.provenance['requirements_review']['passed']
 assert len(b.json_calls)==2 and len(b.calls)==1
 assert 'EVIDENCE-BACKED REQUIREMENTS' in str(b.calls)
 assert 'requirements_extract_s' in doc.provenance['timings']


def test_semantic_rejection_never_becomes_successful_draft(monkeypatch):
 import h3ir.compile as C
 from h3ir.config import get_config
 monkeypatch.setattr(C,'get_config',lambda:replace(get_config(),requirements_review=True))
 monkeypatch.setattr(C,'analyse_all',lambda *a,**k:{})
 b=Pipeline(reject=True)
 with pytest.raises(BackendError,match='unaudited draft'):C.compile_brief(Brief(intent=ONE),backend=b)
 assert len(b.json_calls)==4 and len(b.calls)==3  # extract + 3 audits; compose + 2 corrections


def test_bad_evidence_gets_one_bounded_repair_not_silent_acceptance():
 class Repair(Extractor):
  def json_call(self,messages,*a,**kw):
   self.calls.append(messages)
   obj=ledger()
   if len(self.calls)==1:obj['requirements'][0]['source_quote']='paraphrased evidence'
   return obj
 b=Repair(None);x=R.extract(b,Brief(intent='No music. Cartoon.'))
 assert x['call_count']==2 and len(b.calls)==2
 assert 'paraphrased evidence' in str(b.calls[1])
 assert x['requirements'][0]['source_quote']=='No music. Cartoon.'


def test_successful_audit_still_checks_actual_candidate_evidence():
 b=Brief(intent='The ball must remain visible.')
 bad={'issues':[{'kind':'contradiction','original_quote':'The ball must remain visible.',
                'candidate_quote':'The ball disappears.','explanation':'Keep it visible.'}]}
 backend=Extractor(bad)
 with pytest.raises(BackendError):R.review(backend,b,'The ball stays visible.')
 assert len(backend.calls)==2


def test_semantic_fix_cannot_bypass_mechanical_validation(monkeypatch):
 import h3ir.compile as C
 from h3ir.config import get_config
 monkeypatch.setattr(C,'get_config',lambda:replace(get_config(),requirements_review=True))
 monkeypatch.setattr(C,'analyse_all',lambda *a,**k:{})
 class BrokenRepair(Pipeline):
  def chat(self,*a,**kw):
   if self.calls:self.output='broken document'
   return super().chat(*a,**kw)
 b=BrokenRepair(reject=True)
 with pytest.raises(R.RequirementsReviewFailed) as e:C.compile_brief(Brief(intent=ONE),backend=b)
 assert not e.value.record.get('passed')
 assert len(b.json_calls)==2



def test_visual_audit_guess_is_not_promoted_to_a_new_requirement():
 issue={'kind':'visual_mismatch','original_quote':'match Picture 2','candidate_quote':'OK gesture',
        'explanation':'Replace with a V gesture.'}
 audit={'issues':[issue]};msg=R.findings(audit)[0].msg
 assert 'Replace with a V gesture' not in msg
 assert 'remove the unsupported specifics' in msg
 assert audit['issues'][0]['explanation']=='Replace with a V gesture.'


def test_explicit_constraints_are_valid_source_evidence():
 b=Brief(intent='Cartoon.',constraints=['No music.'])
 obj=ledger('Cartoon.');x=R.extract(Extractor(obj),b)
 assert x['music']['policy']=='forbidden'



def test_reviewer_receives_actual_frame_grid_contract():
 from h3ir.grid import Target
 backend=Extractor({'issues':[]});b=Brief(intent='Make 10 seconds.',seconds=10)
 R.review(backend,b,'tail at 10.13',target=Target.build(10))
 text=str(backend.calls)
 assert 'effective_seconds' in text and '10.125' in text and '243' in text and '10.13' in text
