"""What the HTTP surface says when something the caller can act on goes wrong. No model, no GPU.

`analyse.py` writes its refusals for a person to read -- which file, what is wrong with it, what to
install -- and nothing on the way out caught them. `create_brief` handled `BackendUnavailable` and
`BackendError` only, so every `AssetAnalysisError` became `Internal Server Error` with an empty
body: a corrupt clip, a still attached as `kind: video`, and a machine without ffmpeg all landed
there, and all three messages were discarded.

The two are separated because the answers differ. An unreadable or mis-declared file is the
caller's to fix (422). A missing ffmpeg is this deployment's, and no request they can send will
help (503) -- which is why it is a subclass rather than a phrase in the message.
"""
from __future__ import annotations

import shutil
from pathlib import Path

import pytest
from starlette.testclient import TestClient

from h3ir import service
from h3ir.analyse import AssetAnalysisError, ToolMissing, analyse_all
from h3ir.models import AssetKind, AssetRef, Role

REPO = Path(__file__).resolve().parents[1]
STILL = REPO / "docs/media/plate-car.jpg"


@pytest.fixture()
def client() -> TestClient:
    return TestClient(service.app, raise_server_exceptions=False)


def _body(path: Path, kind: str = "video") -> dict:
    return {"intent": "Edit this clip.", "assets": [{"path": str(path), "kind": kind}]}


# ---------------------------------------------------------------- the analyser's own message

@pytest.mark.skipif(shutil.which("ffprobe") is None, reason="needs a real ffprobe")
def test_a_still_attached_as_a_video_says_which_file_and_which_kind():
    """The most common way into this failure, and the one the bare message did not mention: the
    file is fine and the declared `kind` is wrong. A brief may carry twelve attachments, so the
    error has to name the one that failed rather than leave the caller to guess.

    Real ffmpeg on a real jpg. Nothing is stubbed, because the thing under test is what the
    analyser does with a file it cannot sample.
    """
    ref = AssetRef(kind=AssetKind.VIDEO, role=Role.EDIT_SOURCE, sha256="s" * 64, path=str(STILL))
    with pytest.raises(AssetAnalysisError) as e:
        analyse_all(_never_called_backend(), [ref], use_cache=False)
    msg = str(e.value)
    assert "plate-car.jpg" in msg
    assert "kind: video" in msg, "the declared kind is the thing that is usually wrong"
    assert "kind: image" in msg, "and the message has to say what to do about it"


def test_a_missing_binary_is_its_own_class_and_keeps_it_through_the_asset_annotation(monkeypatch):
    """`ToolMissing` must survive `analyse_all` adding the file name, or the annotation would
    silently downgrade a 503 into a 422 and tell the caller to fix a file that is fine.
    """
    import subprocess

    def no_binary(argv, capture_output=True, text=True, timeout=None):
        raise FileNotFoundError(2, "No such file or directory", argv[0])

    monkeypatch.setattr(subprocess, "run", no_binary)
    ref = AssetRef(kind=AssetKind.VIDEO, role=Role.EDIT_SOURCE, sha256="t" * 64, path=str(STILL))
    with pytest.raises(ToolMissing) as e:
        analyse_all(_never_called_backend(), [ref], use_cache=False)
    assert "not installed" in str(e.value)
    assert "kind: image" not in str(e.value), "installing ffmpeg is the fix, not re-declaring"


def _never_called_backend():
    """A video card is built from sampled frames, and sampling fails before the model is reached.
    Anything that touches this object is a test that is not testing what it says it is."""
    class _Boom:
        def __getattr__(self, name):
            raise AssertionError(f"the backend was called ({name}) before analysis failed")
    return _Boom()


# ---------------------------------------------------------------- the status codes

def test_an_unreadable_asset_is_a_422_carrying_the_analysers_message(client, monkeypatch):
    """The defect: this was a 500 with an empty body. The message is the product here -- it is the
    only thing that tells the caller which attachment to fix -- so it travels verbatim.
    """
    written = ("could not sample a single frame from plate-car.jpg. A video card built without "
               "frames describes nothing (attached as kind: video, /tmp/plate-car.jpg). If it is "
               "a still image, attach it with kind: image instead")

    def boom(*a, **kw):
        raise AssetAnalysisError(written)

    monkeypatch.setattr(service, "compile_brief", boom)
    r = client.post("/v1/briefs", json=_body(STILL))
    assert r.status_code == 422, r.text
    assert r.json()["detail"] == {"code": "asset-unreadable", "message": written, "usage": None}


def test_a_missing_ffmpeg_is_a_503_about_the_deployment(client, monkeypatch):
    """Not a 422. The caller cannot re-declare their way out of a machine with no ffmpeg, and
    telling them their file is bad would send them to fix the wrong thing.
    """
    def boom(*a, **kw):
        raise ToolMissing("ffprobe is not installed, and video references need it. Install "
                          "ffmpeg (it provides both ffmpeg and ffprobe) and try again.")

    monkeypatch.setattr(service, "compile_brief", boom)
    r = client.post("/v1/briefs", json=_body(STILL))
    assert r.status_code == 503, r.text
    assert r.json()["detail"]["code"] == "analysis-tool-missing"
    assert "Install ffmpeg" in r.json()["detail"]["message"]


def test_the_refine_path_answers_the_same_way(client, monkeypatch):
    """PATCH recompiles, so it reaches the analyser too. One of the two endpoints handling this
    and the other not is how a caller learns to distrust the error surface.
    """
    stored = {"brief": None, "doc": None, "at": __import__("time").time(), "versions": 1}
    monkeypatch.setitem(service._STORE, "abc123", stored)

    def boom(*a, **kw):
        raise AssetAnalysisError("the source video is no longer readable (attached as kind: "
                                 "video, /tmp/gone.mp4)")

    monkeypatch.setattr(service, "refine", boom)
    r = client.patch("/v1/briefs/abc123", json={"change": "make it longer"})
    assert r.status_code == 422, r.text
    assert r.json()["detail"]["code"] == "asset-unreadable"


def test_a_body_is_returned_at_all(client, monkeypatch):
    """The shape of the old failure, asserted directly: an empty body is what a caller cannot act
    on, whatever the status code says.
    """
    def boom(*a, **kw):
        raise AssetAnalysisError("unreadable")

    monkeypatch.setattr(service, "compile_brief", boom)
    r = client.post("/v1/briefs", json=_body(STILL))
    assert r.content, "the response had no body"
    assert r.headers["content-type"].startswith("application/json")
