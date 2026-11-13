"""Per-compilation accounting of actual LLM HTTP attempts, including retries."""
from contextvars import ContextVar
from functools import wraps
from threading import Lock

_current = ContextVar("h3ir_usage", default=None)
_attempt = ContextVar("h3ir_usage_attempt", default=None)

class Usage:
    def __init__(self):
        self.lock=Lock();self.calls=0;self.known=0;self.prompt=0;self.completion=0;self.cached=0;self.cache_known=0
    def add(self, value):
        valid=isinstance(value,dict) and all(type(value.get(k)) is int and value[k]>=0 for k in ('prompt_tokens','completion_tokens'))
        if valid and 'total_tokens' in value:
            valid=type(value['total_tokens']) is int and value['total_tokens']==value['prompt_tokens']+value['completion_tokens']
        with self.lock:
            self.calls+=1
            if valid:
                self.known+=1;self.prompt+=value['prompt_tokens'];self.completion+=value['completion_tokens']
                details=value.get('prompt_tokens_details')
                cached=details.get('cached_tokens') if isinstance(details,dict) else None
                if type(cached) is int and 0<=cached<=value['prompt_tokens']:
                    self.cache_known+=1;self.cached+=cached
    def snapshot(self):
        with self.lock:
            return dict(prompt_tokens=self.prompt,completion_tokens=self.completion,
                        total_tokens=self.prompt+self.completion,llm_requests=self.calls,
                        requests_with_usage=self.known,requests_without_usage=self.calls-self.known,
                        complete=self.calls==self.known,
                        prompt_tokens_details={"cached_tokens":self.cached if self.cache_known==self.calls else None})

def reported(value):
    attempt=_attempt.get()
    if attempt is not None:attempt['usage']=value

def account_attempt(fn):
    @wraps(fn)
    def call(*args,**kwargs):
        usage=_current.get()
        if usage is None:return fn(*args,**kwargs)
        state={};token=_attempt.set(state)
        try:return fn(*args,**kwargs)
        finally:
            usage.add(state.get('usage'));_attempt.reset(token)
    return call

def track_compilation(fn):
    @wraps(fn)
    def call(*args,**kwargs):
        usage=Usage();token=_current.set(usage)
        try:
            doc=fn(*args,**kwargs);doc.provenance['usage']=usage.snapshot();return doc
        except Exception as exc:
            exc.usage=usage.snapshot()
            raise
        finally:_current.reset(token)
    return call
