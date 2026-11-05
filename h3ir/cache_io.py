"""Bounded, atomic analysis-card cache for the supported single API process."""
import os
from pathlib import Path
import tempfile
import threading
import time
from .config import get_config

_LOCK = threading.RLock()


def read(path):
    with _LOCK:
        raw = path.read_text()
        os.utime(path, None)
        return raw


def write(path, payload):
    raw = payload.encode()
    cap = get_config().assets.analysis_cache_bytes
    ttl = get_config().assets.analysis_cache_ttl_hours * 3600
    if cap <= 0 or len(raw) > cap:
        return  # Cache admission is optional; compilation remains unchanged.
    with _LOCK:
        entries=[]
        for p in path.parent.glob('*.json'):
            if not p.is_symlink() and p.is_file():
                st=p.stat();entries.append((st.st_mtime,st.st_size,p))
        total=sum(size for _,size,_ in entries)
        for stamp,size,p in sorted(entries):
            if total+len(raw)<=cap and (not ttl or time.time()-stamp<ttl):
                continue
            p.unlink(missing_ok=True);total-=size
        if total+len(raw)>cap:
            return
        fd,name=tempfile.mkstemp(prefix=path.name+'.',suffix='.part',dir=path.parent)
        try:
            with os.fdopen(fd,'wb') as stream:
                stream.write(raw);stream.flush();os.fsync(stream.fileno())
            os.replace(name,path)
        finally:
            Path(name).unlink(missing_ok=True)
