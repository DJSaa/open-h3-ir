from dataclasses import replace
from types import SimpleNamespace
import pytest
from h3ir import professional as P
from h3ir.models import Brief
from h3ir.backend import BackendError
from h3ir.config import get_config
from tests.test_structured_input import ONE, Writer

class Reviewer(Writer):
 def __init__(self,responses=None):super().__init__();self.responses=list(responses or [{'edits':[],'unresolved':[]}]);self.reviews=[]
 def json_call(self,messages,*args,**kw):self.reviews.append(messages);return self.responses.pop(0)

def enable(monkeypatch):
 import h3ir.compile as C
 monkeypatch.setattr(C,'get_config',lambda:replace(get_config(),professional_repair=True,requirements_review=True))
 monkeypatch.setattr(C,'analyse_all',lambda *a,**kw:{})
 return C

def test_valid_professional_kept_exactly_without_composition(monkeypatch):
 C=enable(monkeypatch);b=Reviewer();original=ONE.replace('Quiet room.','Quiet room. No dialogue or subtitles.')
 d=C.compile_brief(Brief(intent=original),backend=b)
 assert d.prompt==original and not b.calls and len(b.reviews)==1
 assert d.provenance['professional_review']['passed']

def test_local_edit_then_recheck(monkeypatch):
 C=enable(monkeypatch);b=Reviewer([{'edits':[{'before':'A ball falls to the floor.','after':'A ball drops to the floor.','reason':'fixture correction'}],'unresolved':[]},{'edits':[],'unresolved':[]}])
 d=C.compile_brief(Brief(intent=ONE),backend=b)
 assert d.prompt==ONE.replace('A ball falls','A ball drops') and len(b.reviews)==2 and not b.calls

@pytest.mark.parametrize('old,new', [('N/A','Music.'),('A ball falls to the floor.','[Shot 2] A ball falls.'),('does not exist','anything')])
def test_forbidden_local_edit_is_rejected(old,new):
 with pytest.raises(BackendError):P.apply_edits(ONE,[{'before':old,'after':new,'reason':'fixture'}])

def test_unresolved_never_returns_original_as_success(monkeypatch):
 C=enable(monkeypatch);b=Reviewer([{'edits':[],'unresolved':['ambiguous instruction']}])
 with pytest.raises(P.RequirementsReviewFailed):C.compile_brief(Brief(intent=ONE),backend=b)

def test_dialogue_is_opaque_and_preserved():
 text=ONE+' <d>[Chinese] 不要改我的话。</d>';masked,values=P.protect(text)
 assert '不要改我的话' not in masked
 with pytest.raises(BackendError):P.apply_edits(masked,[{'before':'{{PROTECTED_SPEECH_1}}','after':'other','reason':'no'}])
 assert list(values.values())==['<d>[Chinese] 不要改我的话。</d>']

def test_plain_input_still_uses_writer(monkeypatch):
 C=enable(monkeypatch)
 monkeypatch.setattr(C,'get_config',lambda:replace(get_config(),professional_repair=True,requirements_review=False))
 b=Writer();d=C.compile_brief(Brief(intent='A ball falls.'),backend=b)
 assert b.calls and not d.provenance.get('professional_review')


def test_invalid_patch_gets_one_explicit_repair(monkeypatch):
 C=enable(monkeypatch)
 good={'edits':[{'before':'A ball falls to the floor.','after':'A ball drops to the floor.','reason':'fixture correction'}],'unresolved':[]}
 b=Reviewer([{'edits':[{'before':'bad','after':'worse'}],'unresolved':[]},good,{'edits':[],'unresolved':[]}])
 d=C.compile_brief(Brief(intent=ONE),backend=b)
 assert 'A ball drops' in d.prompt and len(b.reviews)==3
 assert 'Invalid local edit proposal' in str(b.reviews[1])
 assert len(d.provenance['professional_review']['invalid_proposals'])==1


def test_local_review_requests_schema_enforcement(monkeypatch):
 C=enable(monkeypatch)
 class Strict(Reviewer):
  def json_call(self,messages,*a,**kw):
   assert kw.get('guided') is True
   return super().json_call(messages,*a,**kw)
 assert C.compile_brief(Brief(intent=ONE),backend=Strict()).provenance['professional_review']['passed']
