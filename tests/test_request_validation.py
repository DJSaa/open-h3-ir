"""The request fields a caller can make meaningless. No model and no GPU.

None of the three was checked. `intent: ""` compiled a full brief about nothing. `seconds: 0.0`
returned `ready` with a five-frame, 0.208-second render. `seconds: 1.0` silently became 1.625s, a
62% change, reported only as an INFO about the trained band. And an `aspect` that is not a ratio
reached `grid.canvas_for_aspect`, where the unpack raised ValueError and the caller got a 500.

What is deliberately NOT refused is an unusual ratio. `/v1/capabilities` lists six aspects and the
runtime takes any canvas that is a multiple of 32, so `7:5` is a legal request; refusing it would
repeat the mistake the capacity work just corrected, of reading a published list as a ceiling.
"""
from __future__ import annotations

import pytest
from starlette.testclient import TestClient

from h3ir import service
from h3ir.compile import BriefRefused, check_request
from h3ir.models import Brief


def _client() -> TestClient:
    return TestClient(service.app, raise_server_exceptions=False)


# ---------------------------------------------------------------- intent

def test_an_empty_intent_is_refused():
    with pytest.raises(BriefRefused) as e:
        check_request(Brief(intent="   "))
    assert e.value.code == "intent-empty"
    assert "one field with no default" in str(e.value)


def test_an_empty_intent_over_http_is_a_422_and_not_a_brief_about_nothing():
    r = _client().post("/v1/briefs", json={"intent": ""})
    assert r.status_code == 422, r.text
    assert r.json()["detail"]["code"] == "intent-empty"


# ---------------------------------------------------------------- duration

@pytest.mark.parametrize("seconds", [0.0, -1.0])
def test_a_non_positive_duration_is_refused(seconds):
    with pytest.raises(BriefRefused) as e:
        check_request(Brief(intent="a car pulls away", seconds=seconds))
    assert e.value.code == "duration-invalid"
    assert "0.21s" in str(e.value), "say what the real floor is"


def test_a_legal_short_duration_is_still_allowed():
    """The floor is the runtime's, not a taste judgement: `length` on the sampler node has min=5,
    so five frames is a real render and refusing it would be inventing a limit."""
    check_request(Brief(intent="a car pulls away", seconds=0.21))


def test_a_big_grid_snap_is_reported():
    """1.0s becomes 1.625s. The grid is inherent and the envelope carries both numbers, but a 62%
    change that only ever surfaced as an INFO about training is a change nobody was told about."""
    from h3ir.compile import _assess
    from h3ir.draft import deterministic_draft
    from h3ir.models import Mode
    from h3ir.plan import ProfileOptions

    brief = Brief(intent="a car pulls away", seconds=1.0)
    plan = deterministic_draft(brief, Mode.T2VA, {}, opts=ProfileOptions())
    _, findings, _ = _assess(plan, brief, Mode.T2VA, ProfileOptions(), [])
    hit = [f for f in findings if f.rule == "X19-duration-snapped"]
    assert hit, [str(f) for f in findings]
    assert hit[0].severity == "WARN"
    assert "you asked for 1.000s and the render is 1.625s (39 frames)" in hit[0].msg
    assert "Nearby legal durations" in hit[0].msg


def test_a_small_snap_is_not_reported():
    """5.0 -> 5.167 is 3.3% and inherent to the grid. Warning on every brief would bury the case
    that surprises somebody, which is the only reason the rule exists."""
    from h3ir.compile import _assess
    from h3ir.draft import deterministic_draft
    from h3ir.models import Mode
    from h3ir.plan import ProfileOptions

    brief = Brief(intent="a car pulls away", seconds=5.0)
    plan = deterministic_draft(brief, Mode.T2VA, {}, opts=ProfileOptions())
    _, findings, _ = _assess(plan, brief, Mode.T2VA, ProfileOptions(), [])
    assert not [f for f in findings if f.rule == "X19-duration-snapped"]


# ---------------------------------------------------------------- aspect

@pytest.mark.parametrize("aspect", ["banana", "16/9", "", ":", "16:", "0:0"])
def test_an_aspect_that_is_not_a_ratio_is_refused_rather_than_crashing(aspect):
    """Each of these used to reach canvas_for_aspect and raise out of the compiler as a 500."""
    with pytest.raises(BriefRefused) as e:
        check_request(Brief(intent="a car pulls away", aspect=aspect))
    assert e.value.code == "aspect-invalid"


def test_a_bad_aspect_over_http_is_a_422_with_a_body():
    r = _client().post("/v1/briefs", json={"intent": "a car pulls away", "aspect": "banana"})
    assert r.status_code == 422, r.text
    assert r.json()["detail"]["code"] == "aspect-invalid"
    assert "W:H" in r.json()["detail"]["message"]


@pytest.mark.parametrize("aspect", ["7:5", "16:9", "9:16", "1:1", "1920x1088", "2.35:1"])
def test_a_ratio_outside_the_published_six_is_still_legal(aspect):
    """The runtime takes any canvas that is a multiple of 32, and /v1/capabilities says so in the
    same breath as it lists the six common aspects. A list of suggestions is not a limit."""
    check_request(Brief(intent="a car pulls away", aspect=aspect))


def test_the_wiring_findings_survive_the_written_path(monkeypatch):
    """Where X19 nearly died. The written path builds its finding list from lora_findings plus the
    text validator and never calls `_assess`, so every finding `_assess` added on its own vanished
    the moment the model's brief won -- including X15-audio-uncharacterised, which predates this
    work: a `ready` written brief could carry an audio reference nothing describes and say nothing.
    """
    from h3ir import compile as C
    from h3ir.models import AssetCard, AssetKind, AssetRef, Mode, ModeDecision, Role

    sha = "aud"
    ref = AssetRef(kind=AssetKind.AUDIO, role=Role.BGM, sha256=sha, seconds=3.0)
    cards = {sha: AssetCard(sha256=sha, kind=AssetKind.AUDIO, summary="an audio reference.")}
    brief = Brief(intent="a car pulls away from a kerb", seconds=1.0, assets=[ref])

    class _Backend:
        class cfg:
            model = "test-model"
        def require_available(self): pass
        def server_version(self): return "test"
        def close(self): pass
        def chat(self, *a, **k):
            class R:
                # A brief good enough to survive the validator, so the WRITTEN path is what ships.
                content = ("subject_definitions:\n<Audio 1> is a sound-texture reference for the "
                           "target video.\n\nsummary:\n[audio reuse] The target video shows a car "
                           "pulling away from a kerb with <Audio 1> underneath.\n\n"
                           "retention_analysis:\n<Audio 1>: partially_copy - its music is reused "
                           "beneath the new audio.\n\ndetailed_description:\nThe target video is in "
                           "live-action style.\n[Shot 1] A car pulls away from a kerb at night while "
                           "the camera trucks right with small amplitude at slow speed, holding the "
                           "wet tarmac and the tail lights in frame as the wheels turn.\n\n"
                           "overall_soundscape:\nTyre noise rises over quiet street tone.\n\n"
                           "non_diegetic_music:\nN/A\n")
            return R()

    monkeypatch.setattr(C, "analyse_all", lambda *a, **k: cards)
    monkeypatch.setattr(C, "infer_mode", lambda *a, **k: ModeDecision(
        mode=Mode.REF2VA, confidence=1.0, rule_fired="12.2#1", signals=[]))
    doc = C.compile_brief(brief, backend=_Backend())
    assert doc.source == "written", doc.fallback_reason
    rules = {f.rule for f in doc.findings}
    assert "X19-duration-snapped" in rules, sorted(rules)
    assert "X15-audio-uncharacterised" in rules, sorted(rules)


# ---------------------------------------------------------------- refine

def test_an_empty_refinement_is_refused_before_it_costs_a_recompile(monkeypatch):
    """It used to return 200, report `changed: []`, bump the version and burn a full recompile --
    a model call and a fresh document, to apply nothing."""
    called = []
    monkeypatch.setattr(service, "refine", lambda *a, **k: called.append(1))
    monkeypatch.setitem(service._STORE, "abc123",
                        {"brief": None, "doc": None, "at": __import__("time").time(), "versions": 1})
    r = _client().patch("/v1/briefs/abc123", json={"change": "   "})
    assert r.status_code == 422, r.text
    assert r.json()["detail"]["code"] == "change-empty"
    assert not called, "the recompile ran anyway"


def test_an_unknown_brief_is_still_a_404_before_the_empty_check():
    """Order matters: a caller PATCHing a brief that does not exist gets told that, not told their
    change is empty."""
    r = _client().patch("/v1/briefs/nope", json={"change": ""})
    assert r.status_code == 404, r.text
    assert r.json()["detail"]["code"] == "unknown-brief"
