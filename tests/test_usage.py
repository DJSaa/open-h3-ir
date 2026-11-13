from types import SimpleNamespace
from concurrent.futures import ThreadPoolExecutor
import httpx
import pytest
from h3ir.backend import Backend, BackendError
from h3ir.config import Config, LLMConfig
from h3ir.usage import track_compilation

def reply(p=10,c=3,finish='stop',content='ok',usage=True):
 d={'model':'test','choices':[{'message':{'content':content},'finish_reason':finish}]}
 if usage:d['usage']={'prompt_tokens':p,'completion_tokens':c,'total_tokens':p+c}
 return d

def backend(responses):
 responses=iter(responses)
 def handler(request):
  value=next(responses)
  if isinstance(value,Exception):raise value
  return httpx.Response(200,json=value)
 return Backend(Config(llm=LLMConfig(base_url='http://unit/v1',model='test',max_tokens=100)),client=httpx.Client(transport=httpx.MockTransport(handler)))

@track_compilation
def compile_calls(b,n=1):
 for _ in range(n):b.chat([{'role':'user','content':'test'}],retries=0)
 return SimpleNamespace(provenance={})

def test_aggregates_calls_and_resets_between_tasks():
 b=backend([reply(),reply(20,7),reply(4,2)])
 assert compile_calls(b,2).provenance['usage']==dict(prompt_tokens=30,completion_tokens=10,total_tokens=40,llm_requests=2,requests_with_usage=2,requests_without_usage=0,complete=True,prompt_tokens_details={"cached_tokens":None})
 assert compile_calls(b).provenance['usage']['total_tokens']==6

def test_truncation_and_json_correction_attempts_are_counted():
 b=backend([reply(10,5,'length'),reply(20,8,content='not json'),reply(30,9,content='{"answer":1}')])
 @track_compilation
 def run():
  b.json_call([{'role':'user','content':'test'}],{'type':'object'},required=('answer',),retries=1)
  return SimpleNamespace(provenance={})
 u=run().provenance['usage'];assert u['llm_requests']==3 and u['total_tokens']==82 and u['complete']

@pytest.mark.parametrize('response',[reply(usage=False),reply(p=True),httpx.ReadTimeout('timeout')])
def test_unknown_usage_is_not_a_complete_zero(response):
 b=backend([reply(),response])
 try:u=compile_calls(b,2).provenance['usage']
 except BackendError as e:u=e.usage
 assert u['total_tokens']==13 and u['llm_requests']==2 and u['requests_without_usage']==1 and not u['complete']

def test_concurrent_compilations_are_isolated():
 def task(n):return compile_calls(backend([reply(n,1)]),1).provenance['usage']['total_tokens']
 with ThreadPoolExecutor(max_workers=4) as e:assert list(e.map(task,range(1,9)))==list(range(2,10))

def test_zero_calls_represent_no_model_work():
 assert compile_calls(backend([]),0).provenance['usage']==dict(prompt_tokens=0,completion_tokens=0,total_tokens=0,llm_requests=0,requests_with_usage=0,requests_without_usage=0,complete=True,prompt_tokens_details={"cached_tokens":0})

def test_real_compile_serializes_aggregate(monkeypatch):
 import h3ir.compile as C
 from h3ir.models import Brief
 from h3ir.service import _envelope
 from tests.test_structured_input import ONE
 b=backend([reply(23,11,content=ONE)])
 monkeypatch.setattr(b,'require_available',lambda:None);monkeypatch.setattr(b,'server_version',lambda:'unit')
 monkeypatch.setattr(C,'analyse_all',lambda *a,**k:{})
 brief=Brief(intent='A ball falls.');doc=C.compile_brief(brief,backend=b)
 assert _envelope('unit',doc,brief)['usage']['total_tokens']==34


def test_failure_api_preserves_partial_usage(monkeypatch):
 from starlette.testclient import TestClient
 from h3ir import service
 from h3ir.requirements import RequirementsReviewFailed
 u=dict(prompt_tokens=5,completion_tokens=3,total_tokens=8,llm_requests=2,requests_with_usage=1,requests_without_usage=1,complete=False)
 def fail(*a,**k):
  e=RequirementsReviewFailed('rejected',{},'candidate');e.usage=u;raise e
 monkeypatch.setattr(service,'compile_brief',fail)
 r=TestClient(service.app).post('/v1/briefs',json={'intent':'test'})
 assert r.status_code==502 and r.json()['detail']['usage']==u


@pytest.mark.parametrize('counts,expected',[([7,0],7),([7,None],None),([7,True],None),([7,21],None)])
def test_cached_input_tokens_are_a_subset_not_extra_usage(counts,expected):
    responses=[]
    for count in counts:
        response=reply(20,3);response['usage']['prompt_tokens_details']={'cached_tokens':count};responses.append(response)
    u=compile_calls(backend(responses),2).provenance['usage']
    assert u['prompt_tokens_details']['cached_tokens']==expected
    assert u['prompt_tokens']==40 and u['completion_tokens']==6 and u['total_tokens']==46
    assert u['complete'] is True
