"""Transport-independent compilation entry, shared by HTTP and runtime Workers.

Shared assets are supplied by an already validated resolver, never arbitrary
paths from the caller. The normal upload validation remains active for HTTP.
"""
from contextvars import ContextVar
from contextlib import contextmanager
from dataclasses import dataclass
import json
import uuid

_sources = ContextVar('h3ir_shared_sources', default=None)
_hooks = ContextVar('h3ir_execution_hooks', default=None)

class CompilationCancelled(RuntimeError):
    pass

def shared_source(digest):
    sources = _sources.get()
    return sources.get(digest) if sources is not None else None

def checkpoint():
    hooks = _hooks.get()
    if hooks is not None:
        hooks.check_cancelled()

def stage_read(name, fingerprint):
    hooks = _hooks.get()
    return hooks.stage_read(name, fingerprint) if hooks is not None and hasattr(hooks, 'stage_read') else None

def stage_write(name, fingerprint, result):
    hooks = _hooks.get()
    if hooks is not None and hasattr(hooks, 'stage_write'):
        hooks.stage_write(name, fingerprint, result)

@contextmanager
def execution_hooks(hooks):
    token = _hooks.set(hooks)
    try:
        yield
    finally:
        _hooks.reset(token)

def call_started(body=None):
    checkpoint()
    hooks = _hooks.get()
    if hooks is None:return None
    messages=body.get("messages",[]) if isinstance(body,dict) else []
    image_items=sum(1 for message in messages if isinstance(message,dict)
                    for item in (message.get("content",[]) if isinstance(message.get("content"),list) else [])
                    if isinstance(item,dict) and item.get("type")=="image_url")
    return hooks,hooks.call_started({"image_items":image_items,"message_count":len(messages)})

def call_reported(handle, usage):
    if handle is not None:
        handle[0].call_reported(handle[1], usage)

@dataclass(frozen=True)
class CompilationResult:
    status_code: int
    body: dict

def compile_request(body, *, sources=None, hooks=None, lease_token=None):
    # Lazy import permits the HTTP facade to call this same entry.
    from . import service, asset_leases
    request = body if isinstance(body, service.BriefIn) else service.BriefIn.model_validate(body)
    source_token = _sources.set(sources)
    token = lease_token or 'local-' + uuid.uuid4().hex
    own_lease = sources is None and lease_token is None
    digests = [d for a in request.assets for d in (a.sha256, a.paired_video_sha256) if d]
    try:
        service._require_uploaded(request)
        if sources is not None and any(d not in sources for d in digests):
            raise ValueError('shared asset resolver does not cover request')
        if own_lease:
            asset_leases.reserve(token, digests)
        workspace = (asset_leases.compiling(token, digests) if sources is None
                     else asset_leases.shared_workspace(token))
        with workspace, execution_hooks(hooks):
            checkpoint()
            response = service._create_brief(request)
            checkpoint()
            return CompilationResult(response.status_code, json.loads(response.body))
    finally:
        _sources.reset(source_token)
        if own_lease:
            asset_leases.release(token)
