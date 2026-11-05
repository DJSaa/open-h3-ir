r"""The upload store: bytes a caller sent, kept under the name their own content gives them.

Attachments used to reach this service one way only, as a path it opened off its own filesystem.
That works when the caller and the service see one disk and it cannot be made to work otherwise:
ComfyUI on one machine pointed at a service on another has no spelling of C:\ComfyUI\temp\ref.png
the service can open, because a path is not a file. This module is the other way in, for the caller
that has the bytes and no shared disk.

Three decisions hold it together, and each one is load-bearing rather than tidy.

**A stored file is named by the digest THIS PROCESS computed, never by the one the caller claimed.**
The caller names its PUT with a sha256 so the transfer can be skipped when we already hold those
bytes, but that name never reaches the filesystem. The bytes are streamed through hashlib into a
temporary file, the digest is compared with the claim, and the file is placed under the digest we
computed. So the path is derived from content we hashed ourselves, and a name meant to escape the
store, a traversal or an absolute path or a device file, never gets that far: the only shape accepted
here is 64 lowercase hex characters.

**Content addressing rather than an upload id.** The service already keys everything by sha256:
asset cards, the transcript map, the wiring manifest, the node's own slot bindings. An upload id
would be a second name for a thing that already has one, and it would break the property that makes
a big file bearable: re-queueing a graph whose clip has not changed sends no bytes at all, because
the digest is the same and the store already answers to it.

**Both ceilings are enforced against bytes actually received.** `Content-Length` is a claim: it is
absent under chunked encoding and it can simply be wrong. It is used to refuse early and to reserve
room, and never as the measurement. The count that stops a write is the one this module accumulated.

Nothing here ever serves bytes back out. The store is write-and-analyse, not a file server: a read
endpoint would turn any service on a network into an anonymous drop box that hands the drop back.
"""
from __future__ import annotations

import hashlib
import logging
import os
import re
import tempfile
import threading
import time
from pathlib import Path

from .config import get_config

log = logging.getLogger("h3ir.uploads")

# The only shape a stored asset's name can have. Anchored, lowercase, exact length: `sha256` is
# spelled one way by every producer of one, and accepting a second spelling would mean two names for
# one file and a cache that misses half the time.
DIGEST = re.compile(r"\A[0-9a-f]{64}\Z")

# An entry this new is never evicted, however full the store is. Eviction and use race otherwise: a
# brief that referenced an asset a moment ago is about to spend a minute in the analyser and the
# prose model, and having its attachment deleted underneath it by another caller's upload would be a
# failure nobody could reproduce. Fifteen minutes is longer than any compile measured here.
EVICT_GRACE_S = 900.0

# A part-written upload whose request died leaves one of these behind. Swept on the same grace, so a
# crash costs disk until the next upload and no longer.
PARTIAL_PREFIX = "incoming-"
PARTIAL_SUFFIX = ".part"

_LOCK = threading.RLock()
# Bytes promised to uploads currently in flight, counted against the store cap for the same reason
# the cap exists at all: without it, twenty parallel uploads each read a store that is under the cap
# and each write a full file, and the cap bounds nothing. Reserved at the start and released at the
# end, so the arithmetic is one lock acquisition per upload rather than one per chunk.
_INFLIGHT: dict[int, int] = {}


class UploadRefused(RuntimeError):
    """Base for every refusal this module makes. The message is written for whoever sent the bytes.

    Subclassed rather than carrying a code, because the four cases need four different HTTP statuses
    and four different next actions, and `service.py` spells the code beside the status where the
    node's own test can read it out of the source.
    """


class NotADigest(UploadRefused):
    """The name in the request is not a sha256. Raised before a single byte is read."""


class TooLarge(UploadRefused):
    """One file is over `H3IR_UPLOAD_MAX_BYTES`."""


class StoreFull(UploadRefused):
    """The store is at `H3IR_UPLOAD_STORE_BYTES` and eviction could not free enough room."""


class DigestMismatch(UploadRefused):
    """The bytes that arrived are not the bytes the name claimed."""


# --------------------------------------------------------------------------- where a file lives

def store_dir() -> Path:
    d = get_config().paths.uploads_dir()
    d.mkdir(parents=True, exist_ok=True)
    return d


def is_digest(name: str) -> bool:
    return bool(DIGEST.match(name or ""))


def path_for(sha256: str) -> Path:
    """Where an asset with this digest lives, and the only place such a path is constructed.

    Sharded on the first two characters so a store with tens of thousands of assets never has one
    directory with tens of thousands of entries in it, which is slow to list on every sweep.
    """
    if not is_digest(sha256):
        raise NotADigest(
            f"{sha256[:80]!r} is not a sha256. An uploaded attachment is named by the hash of its "
            "own bytes, written as 64 lowercase hexadecimal characters.")
    return store_dir() / sha256[:2] / sha256


def stored(sha256: str) -> Path | None:
    """The stored file for this digest, or None. Raises `NotADigest` for a name that is not one."""
    p = path_for(sha256)
    return p if p.is_file() else None


def missing(digests: list[str]) -> list[str]:
    """Which of these the store does not hold, in the order given and without repeats."""
    out: list[str] = []
    for sha in digests:
        if sha not in out and stored(sha) is None:
            out.append(sha)
    return out


def touch(path: Path | str) -> None:
    """Mark an asset as used, so eviction takes the least recently USED rather than the oldest.

    Called when a brief references an asset, which is what makes the file somebody keeps compiling
    against the last one to go, however long ago they uploaded it.
    """
    try:
        os.utime(path, None)
    except OSError:  # noqa: BLE001 - a stamp we cannot write is not a reason to fail a compile
        pass


# --------------------------------------------------------------------------- what the store costs

def _entries() -> list[tuple[Path, os.stat_result]]:
    root = store_dir()
    out: list[tuple[Path, os.stat_result]] = []
    for shard in root.iterdir():
        if not shard.is_dir():
            continue
        for p in shard.iterdir():
            try:
                out.append((p, p.stat()))
            except OSError:  # noqa: BLE001 - it went away underneath us, which is the sweep's job
                continue
    return out


def total_bytes() -> int:
    return sum(st.st_size for _, st in _entries())


def _unlink(p: Path) -> None:
    try:
        p.unlink()
    except OSError:  # noqa: BLE001 - already gone is the outcome we wanted
        pass


def _sweep_locked(*, reserve: int = 0, now: float | None = None) -> dict[str, int]:
    """Drop what has expired, then the least recently used, until the store fits under its cap.

    `reserve` is room to leave for an upload that is about to start. Returns what happened, which is
    logged rather than reported to the caller: which of somebody else's assets aged out is not their
    business and not something they can act on.

    Nothing inside `EVICT_GRACE_S` is touched, in either pass. That is what makes the store safe to
    sweep on the request path instead of on a timer nobody remembers to start.

    Safe to run concurrently with itself: an entry another sweep already removed is the outcome this
    one wanted, which is why `_unlink` does not care.
    """
    cfg = get_config().assets
    now = now if now is not None else time.time()
    ttl = cfg.upload_ttl_hours * 3600
    freed = dropped = 0

    items = sorted(_entries(), key=lambda t: t[1].st_mtime)
    total = sum(st.st_size for _, st in items)
    keep: list[tuple[Path, os.stat_result]] = []
    for p, st in items:
        age = now - st.st_mtime
        from .asset_leases import pinned
        if pinned(p.name):
            keep.append((p, st))
            continue
        if ttl and age > ttl and age > EVICT_GRACE_S:
            _unlink(p)
            dropped, freed, total = dropped + 1, freed + st.st_size, total - st.st_size
        else:
            keep.append((p, st))

    want = cfg.upload_store_bytes - reserve
    for p, st in keep:                                  # least recently used first
        if total <= want:
            break
        from .asset_leases import pinned
        if pinned(p.name) or now - st.st_mtime <= EVICT_GRACE_S:
            continue
        _unlink(p)
        dropped, freed, total = dropped + 1, freed + st.st_size, total - st.st_size

    for p in store_dir().glob(f"{PARTIAL_PREFIX}*{PARTIAL_SUFFIX}"):
        try:
            if now - p.stat().st_mtime > EVICT_GRACE_S:
                _unlink(p)
        except OSError:  # noqa: BLE001
            continue

    if dropped:
        log.info("upload store: evicted %d asset(s), freed %d bytes, %d bytes remain",
                 dropped, freed, total)
    return {"dropped": dropped, "freed": freed, "bytes": total}


# --------------------------------------------------------------------------- taking the bytes

class Receiver:
    """One upload in progress: hash the bytes on the way past, then place them under their digest.

    Deliberately synchronous and free of any HTTP type, so the transport drives it and this can be
    tested by handing it a list of byte strings. `service.py` owns the request stream; this owns
    every rule about what may land on the disk.

    Use it as a context manager. An upload that raises anywhere -- over the cap, wrong digest, a
    connection that died mid-body -- must leave nothing behind, and `__exit__` is what
    guarantees that without every caller remembering to.
    """

    def __init__(self, claimed: str, *, allowance: int, ticket: int):
        self.claimed = claimed
        self.allowance = allowance
        self.received = 0
        self._ticket = ticket
        self._digest = hashlib.sha256()
        self._done = False
        fd, tmp = tempfile.mkstemp(dir=str(store_dir()), prefix=PARTIAL_PREFIX,
                                   suffix=PARTIAL_SUFFIX)
        # Beside the store rather than in the system temp, so placing the finished file is a rename
        # within one filesystem. Across filesystems it would be a copy, which is not atomic: a
        # reader could open a half-written asset and the analyser would describe a truncated image.
        self._tmp = Path(tmp)
        self._fh = os.fdopen(fd, "wb")

    def write(self, chunk: bytes) -> None:
        if self._done:
            raise RuntimeError("internal: this upload has already been finished")
        self.received += len(chunk)
        if self.received > self.allowance:
            cap = get_config().assets.upload_max_bytes
            if self.allowance >= cap:
                raise TooLarge(
                    f"the attachment is larger than the {cap} bytes this service accepts for one "
                    "file. Raise H3IR_UPLOAD_MAX_BYTES where the service runs, or send a shorter "
                    "or more compressed version of it.")
            raise StoreFull(
                "the attachment is larger than it said it was, and the extra bytes do not fit "
                "in the room reserved for it. Send it again.")
        self._digest.update(chunk)
        self._fh.write(chunk)

    def finish(self) -> tuple[Path, int]:
        """Verify and place the file. Returns where it landed and how many bytes it is."""
        self._fh.close()
        self._done = True
        computed = self._digest.hexdigest()
        if computed != self.claimed:
            _unlink(self._tmp)
            _release(self._ticket)
            raise DigestMismatch(
                f"the bytes that arrived hash to {computed}, and this upload was named "
                f"{self.claimed}. Either the file changed while it was being sent, or the transfer "
                "was truncated. Send it again.")
        # The digest THIS process computed, not the one the request carried. They are equal by the
        # check above; using the computed one is what makes the path independent of the request.
        dest = path_for(computed)
        dest.parent.mkdir(parents=True, exist_ok=True)
        os.replace(self._tmp, dest)
        _release(self._ticket)
        log.info("stored upload %s (%d bytes)", computed[:12], self.received)
        return dest, self.received

    def abort(self) -> None:
        if self._done:
            return
        self._done = True
        try:
            self._fh.close()
        except OSError:  # noqa: BLE001
            pass
        _unlink(self._tmp)
        _release(self._ticket)

    def __enter__(self) -> Receiver:
        return self

    def __exit__(self, *exc: object) -> None:
        self.abort()


def _release(ticket: int) -> None:
    with _LOCK:
        _INFLIGHT.pop(ticket, None)


def receiver(claimed: str, *, declared: int | None = None) -> Receiver:
    """Open a `Receiver` for these bytes, or refuse before any of them are read.

    `declared` is the request's own `Content-Length` when it has one. It buys two things and is
    trusted for neither: an oversized file is refused without transferring it, and the store cap can
    reserve the right amount instead of assuming the worst. A caller that lies low still hits the
    allowance below, which counts what actually arrived.
    """
    cfg = get_config().assets
    if not is_digest(claimed):
        raise NotADigest(
            f"{claimed[:80]!r} is not a sha256, so it cannot name an uploaded attachment. Name the "
            "upload with the hash of the file's own bytes, as 64 lowercase hexadecimal characters.")
    if cfg.upload_max_bytes <= 0:
        raise TooLarge(
            "this service does not accept uploads: it was started with H3IR_UPLOAD_MAX_BYTES set "
            "to 0. Either set that to the largest file it should take, or attach files by a path "
            "the service itself can open.")
    if declared is not None and declared > cfg.upload_max_bytes:
        raise TooLarge(
            f"the attachment is {declared} bytes and this service accepts {cfg.upload_max_bytes} "
            "bytes for one file. Raise H3IR_UPLOAD_MAX_BYTES where the service runs, or send a "
            "shorter or more compressed version of it.")

    # Pessimistic when the size is unknown, because the alternative is reserving nothing and
    # discovering the store is full with the file already written.
    want = declared if declared is not None else cfg.upload_max_bytes
    with _LOCK:
        promised = sum(_INFLIGHT.values())
        sweep(reserve=promised + want)
        room = cfg.upload_store_bytes - total_bytes() - promised
        if want > room:
            raise StoreFull(
                f"there is no room for another {want} bytes: this service keeps at most "
                f"{cfg.upload_store_bytes} bytes of uploaded attachments and the rest are in use. "
                "They are dropped once they are 48 hours old by default, so waiting works; so does "
                "raising H3IR_UPLOAD_STORE_BYTES where the service runs.")
        ticket = _next_ticket()
        _INFLIGHT[ticket] = want
    try:
        return Receiver(claimed, allowance=min(want, cfg.upload_max_bytes), ticket=ticket)
    except BaseException:
        # Opening the temporary file can fail -- a full disk, a read-only state directory -- and a
        # reservation nobody releases would shrink the store's usable room for the life of the
        # process, one dead upload at a time.
        _release(ticket)
        raise


_TICKET = 0


def _next_ticket() -> int:
    """Called with `_LOCK` held."""
    global _TICKET
    _TICKET += 1
    return _TICKET


def sweep(*, reserve: int = 0, now: float | None = None) -> dict[str, int]:
    # Reserve/pin and eviction share one RLock, including the actual unlink.
    with _LOCK:
        return _sweep_locked(reserve=reserve, now=now)
