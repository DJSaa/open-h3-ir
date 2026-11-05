"""Single-API-process upload pins, shared by upload eviction and compiler calls.

Preparing leases expire; an executing lease never expires underneath a compiler.
Tokens identify one attempt and cannot start a second simultaneous compile.
"""
from contextlib import contextmanager
import re
import time
import contextvars
import shutil
from .config import get_config
from fastapi import HTTPException
from . import uploads

_LEASES = {}
_WORKSPACE = contextvars.ContextVar("h3ir_compile_workspace", default=None)

def analysis_workspace():
    return _WORKSPACE.get()

PREPARE_TTL_S = 1200
MAX_LEASES = 256


def _expire():
    now = time.monotonic()
    for key, entry in list(_LEASES.items()):
        if not entry["active"] and entry["deadline"] < now:
            del _LEASES[key]


def reserve(token, digests):
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", token) or not isinstance(digests, list) or len(digests) > 32 or any(not uploads.is_digest(d) for d in digests):
        raise HTTPException(422, detail={"code": "invalid-asset-lease"})
    with uploads._LOCK:
        _expire()
        old = _LEASES.get(token)
        wanted = set(digests)
        if old and old["digests"] != wanted:
            raise HTTPException(409, detail={"code": "asset-lease-conflict"})
        if old is None:
            if len(_LEASES) >= MAX_LEASES:
                raise HTTPException(503, detail={"code": "asset-lease-capacity"})
            _LEASES[token] = {"digests": wanted, "active": False,
                              "deadline": time.monotonic()+PREPARE_TTL_S}
        elif not old["active"]:
            old["deadline"] = time.monotonic()+PREPARE_TTL_S


def pinned(digest):
    with uploads._LOCK:
        _expire()
        return any(digest in entry["digests"] for entry in _LEASES.values())


def release(token):
    with uploads._LOCK:
        entry = _LEASES.get(token)
        if entry and entry["active"]:
            raise HTTPException(409, detail={"code": "asset-lease-still-executing"})
        _LEASES.pop(token, None)


@contextmanager
def compiling(token, digests):
    with uploads._LOCK:
        _expire()
        entry = _LEASES.get(token)
        if entry is None or not set(digests) <= entry["digests"]:
            raise HTTPException(409, detail={"code": "asset-lease-missing"})
        if entry["active"]:
            raise HTTPException(409, detail={"code": "asset-lease-already-executing"})
        if uploads.missing(list(entry["digests"])):
            raise HTTPException(422, detail={"code": "asset-not-uploaded"})
        entry["active"] = True
    workspace = get_config().paths.state_dir / "compiler-work" / token
    workspace.mkdir(parents=True, exist_ok=True)
    context_token = _WORKSPACE.set(workspace)
    try:
        yield
    finally:
        _WORKSPACE.reset(context_token)
        shutil.rmtree(workspace, ignore_errors=True)
        with uploads._LOCK:
            entry["active"] = False
            entry["deadline"] = time.monotonic()+PREPARE_TTL_S
