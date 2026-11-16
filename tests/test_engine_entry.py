"""Transport parity and context isolation without a model."""
import json
from concurrent.futures import ThreadPoolExecutor
from fastapi.responses import JSONResponse
import pytest
from h3ir import engine, service


def test_http_and_library_use_same_entry(monkeypatch):
    monkeypatch.setattr(service,'_create_brief',lambda body:JSONResponse({'echo':body.intent},status_code=201))
    body=service.BriefIn(intent='hello')
    assert engine.compile_request(body).body=={'echo':'hello'}
    assert json.loads(service.create_brief(body).body)=={'echo':'hello'}


def test_shared_sources_do_not_leak_into_http_or_other_threads(monkeypatch,tmp_path):
    digest='a'*64;p=tmp_path/'asset';p.write_bytes(b'bytes')
    body=service.BriefIn(intent='hello',assets=[{'sha256':digest,'kind':'image'}])
    monkeypatch.setattr(service,'_create_brief',lambda body:JSONResponse({'source':str(service._source_of(body.assets[0])[0])},status_code=201))
    result=engine.compile_request(body,sources={digest:p})
    assert result.body['source']==str(p)
    assert engine.shared_source(digest) is None
    with pytest.raises(Exception):engine.compile_request(body)


def test_cancel_checkpoint_prevents_next_model_call():
    from h3ir.usage import account_attempt
    class Hooks:
        def check_cancelled(self):raise engine.CompilationCancelled()
        def call_started(self):raise AssertionError('must not start')
    @account_attempt
    def call():raise AssertionError('model must not run')
    with engine.execution_hooks(Hooks()),pytest.raises(engine.CompilationCancelled):call()


@pytest.fixture(autouse=True)
def isolated_config(tmp_path):
    from dataclasses import replace
    from h3ir.config import get_config,set_config
    old=get_config()
    set_config(replace(old,paths=replace(old.paths,state_dir=tmp_path/'state')))
    try:yield
    finally:set_config(old)
