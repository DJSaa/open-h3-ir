# SPDX-FileCopyrightText: Copyright (c) 2026 Dingo contributors
# SPDX-License-Identifier: Apache-2.0
"""Offline regression-harness checks; HTTP is mocked, no remote model calls."""
import importlib.util
import json
from pathlib import Path
import sys
from types import SimpleNamespace
import httpx
import pytest

REGRESSION = Path(__file__).resolve().parent / 'regression'


@pytest.fixture
def tools(monkeypatch):
    def module(name):
        spec = importlib.util.spec_from_file_location('regression_'+name, REGRESSION/(name+'.py'))
        value = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(value)
        monkeypatch.setitem(sys.modules,name,value)
        return value
    common=module('common')
    report=module('report')
    return SimpleNamespace(common=common,report=report,run=module('run'),
                           validate=module('validate'),assets=module('import_assets'))


def test_frozen_corpus_counts_order_and_bundled_asset_hashes(tools):
    value=tools.validate.validate()
    assert value['cases']==126 and value['normal']==121 and value['negative']==5
    assert value['assets']==25 and value['output_fixtures']==6
    assert len(value['missing_external_assets']) in (0,1,2,3)
    catalog=tools.common.load(REGRESSION/'cases/index.json')
    r04=next(c for c in catalog['cases'] if c['id']=='B-R04-A')
    req=tools.common.load(REGRESSION/r04['request_file'])
    assert [f['kind'] for f in req['files']]==['image','image','image','audio','audio']
    assert req['gateway_fields']['reference_options']==json.dumps([
        {'index':3,'role':'voice_timbre'},{'index':4,'role':'voice_timbre'}],ensure_ascii=False)


def test_modified_bytes_and_escaping_paths_are_rejected(tools,tmp_path):
    path=tmp_path/'asset';path.write_bytes(b'original')
    meta={'path':'asset','bytes':8,'sha256':tools.common.digest(path)}
    assert tools.common.asset_path(meta,tmp_path)==path
    path.write_bytes(b'modified')
    with pytest.raises(ValueError,match='checksum'):tools.common.asset_path(meta,tmp_path)
    with pytest.raises(ValueError,match='escapes'):tools.common.owned(tmp_path,'../outside')


def test_external_import_requires_exact_hash_and_does_not_modify_source(tools,tmp_path):
    root=tmp_path/'package';source=tmp_path/'source';source.mkdir()
    original=source/'mouse.jpg';original.write_bytes(b'expected image bytes')
    sha=tools.common.digest(original)
    tools.common.save(root/'assets/manifest.json',{sha:{'bundled':False,'path':'assets/local/'+sha+'.jpg','bytes':original.stat().st_size}})
    assert tools.assets.import_assets(source,root)==[]
    assert (root/('assets/local/'+sha+'.jpg')).read_bytes()==original.read_bytes()
    assert original.read_bytes()==b'expected image bytes'


@pytest.mark.parametrize('http,body,transport,outcome',[
    (201,{'status':'ready','source':'written','ir':{'prompt':'valid'}},'direct','PASS_TECHNICAL'),
    (201,{'status':'degraded','source':'draft','ir':{'prompt':'fallback'}},'direct','DEGRADED'),
    (200,{'status':'completed','content':{'prompt':'fallback'},'warnings':[{'code':'context_ir_degraded_result'}]},'gateway','DEGRADED'),
    (201,{'status':'ready','source':'written','ir':{'prompt':' '}},'direct','FAIL_EMPTY_PROMPT'),
    (200,{'status':'failed','error':{'code':'context_ir_execution_unknown'}},'gateway','FAIL_TASK'),
])
def test_quality_is_not_inferred_from_success_http(tools,http,body,transport,outcome):
    assert tools.common.classify({'negative':False},http,body,transport)==outcome


def test_negative_infrastructure_failure_is_not_credited_as_rejection(tools):
    case={'negative':True}
    assert tools.common.classify(case,422,{'detail':{'code':'intent-empty'}},'direct')=='PASS_REJECTION'
    assert tools.common.classify(case,500,{},'direct')=='FAIL_NEGATIVE_INPUT'
    assert tools.common.classify(case,201,{'status':'ready','source':'written'},'direct')=='FAIL_NEGATIVE_INPUT'


def install_http(tools,monkeypatch,handler):
    original=httpx.Client
    monkeypatch.setattr(tools.run.httpx,'Client',lambda **kwargs:original(transport=httpx.MockTransport(handler),**kwargs))


def test_direct_run_and_resume_do_not_resubmit_completed_work(tools,monkeypatch,tmp_path):
    calls=[]
    token='unit-test-placeholder'
    monkeypatch.setenv('REGRESSION_TOKEN',token)
    def handler(request):
        calls.append(request)
        assert request.headers['Authorization']=='Bearer '+token
        assert request.url.path=='/v1/briefs'
        return httpx.Response(201,json={'status':'ready','source':'written','ir':{'prompt':'A static scene.'},'usage':{'total_tokens':10}})
    install_http(tools,monkeypatch,handler)
    args=['--base-url','https://compiler.example.com','--cases','T01','--output',str(tmp_path/'run')]
    assert tools.run.main(args)==0
    assert tools.run.main(args+['--resume'])==0
    assert len(calls)==1
    for path in (tmp_path/'run').rglob('*'):
        if path.is_file():
            text=path.read_text()
            assert token not in text and 'https://compiler.example.com' not in text


@pytest.mark.parametrize('degraded',[False,True])
def test_gateway_async_query_and_degraded_warning(tools,monkeypatch,tmp_path,degraded):
    calls=[]
    def handler(request):
        calls.append(request)
        if request.method=='POST':
            assert request.url.path=='/v1/context-ir'
            assert 'Idempotency-Key' in request.headers
            assert b'name="prompt"' in request.content
            return httpx.Response(202,json={'id':'context-ir-test','status':'queued'})
        assert request.url.path=='/v1/context-ir/context-ir-test'
        return httpx.Response(200,json={'id':'context-ir-test','status':'completed','content':{'prompt':'A static scene.'},
                                      'warnings':[{'code':'context_ir_degraded_result'}] if degraded else []})
    install_http(tools,monkeypatch,handler)
    code=tools.run.main(['--base-url','https://gateway.example.com','--transport','gateway','--cases','T01','--output',str(tmp_path/'run')])
    assert code==int(degraded)
    result=tools.common.load(tmp_path/'run/cases/T01/result.json')
    assert result['outcome']==('DEGRADED' if degraded else 'PASS_TECHNICAL')
    assert len(calls)==2


def test_direct_lost_response_is_not_blindly_retried(tools,monkeypatch,tmp_path):
    calls=[]
    def handler(request):
        calls.append(request)
        raise httpx.ReadTimeout('lost response',request=request)
    install_http(tools,monkeypatch,handler)
    args=['--base-url','https://compiler.example.com','--cases','T01','--output',str(tmp_path/'run')]
    assert tools.run.main(args)==1
    assert tools.run.main(args+['--resume'])==1
    assert len(calls)==1
    assert tools.common.load(tmp_path/'run/cases/T01/result.json')['outcome']=='EXECUTION_UNKNOWN'


def test_gateway_resume_polls_original_task_after_lost_query(tools,monkeypatch,tmp_path):
    calls=[]
    def handler(request):
        calls.append(request)
        if request.method=='POST':return httpx.Response(202,json={'id':'context-ir-test','status':'queued'})
        if len(calls)==2:raise httpx.ReadTimeout('lost query',request=request)
        return httpx.Response(200,json={'id':'context-ir-test','status':'completed','content':{'prompt':'valid'},'warnings':[]})
    install_http(tools,monkeypatch,handler)
    args=['--base-url','https://gateway.example.com','--transport','gateway','--cases','T01','--output',str(tmp_path/'run')]
    assert tools.run.main(args)==1
    assert tools.run.main(args+['--resume'])==0
    assert [r.method for r in calls]==['POST','GET','GET']


def test_report_escapes_model_content_and_flags_known_negative(tools,tmp_path):
    folder=tmp_path/'cases/N01'
    tools.common.save(folder/'result.json',{'id':'N01','outcome':'FAIL_NEGATIVE_INPUT','elapsed_s':1})
    tools.common.save(folder/'body.json',{'prompt':'<script>alert(1)</script>'})
    summary=tools.report.report(tmp_path)
    text=(tmp_path/'index.html').read_text()
    assert '<script>' not in text and '&lt;script&gt;' in text
    assert summary['outcomes']=={'FAIL_NEGATIVE_INPUT':1}
    assert summary['semantic_quality']=='NOT_REVIEWED'


def test_unknown_case_and_changed_resume_configuration_refused(tools,monkeypatch,tmp_path):
    with pytest.raises(ValueError,match='unknown case'):tools.common.select_cases(ids=['not-a-case'])
    install_http(tools,monkeypatch,lambda r:httpx.Response(201,json={'status':'ready','source':'written','ir':{'prompt':'valid'}}))
    args=['--base-url','https://compiler.example.com','--cases','T01','--output',str(tmp_path/'run')]
    assert tools.run.main(args)==0
    with pytest.raises(SystemExit):tools.run.main(args+['--resume','--seed','23'])
