"""The additive path raised `NameError` on entry and 680 green tests did not notice.

`prose.beat_sheet` and `prose.shot_body` both interpolated `{dial}` into their ask while the
surrounding code defined `dlg`. Both raise on the statement that builds the ask, before any model is
called. They are reachable only through `compile_brief(write_first=False)`, a default-True parameter
that neither `service.create_brief` nor the CLI ever sets, so the branch is unreachable in production
and the bug was latent -- and unreachable code is exactly what no test exercised.

Leaving a `NameError` behind a flag is the shape that bites whoever next adds a caller, so the name is
fixed and these two tests are the ones that would have caught it: they call each function with a
backend that records the ask and returns a minimal well-formed reply. No model, no GPU.
"""
from __future__ import annotations

from h3ir.models import (AssetCard, AssetKind, AssetRef, Brief, CameraMove, DialogueLine, Mode,
                         Role)
from h3ir.plan import ProfileOptions, build_plan
from h3ir.prose import beat_sheet, shot_body

LINE = DialogueLine(text="First batch of the morning.", speaker_hint="the baker")


class _Stub:
    """Records what it was asked and answers in the shape each call expects."""

    class _Cfg:
        model = "stub"

    cfg = _Cfg()

    class _Reply:
        content = ("The baker sets a tray on the counter and {{CAM}}, steam lifting off the crust "
                   "as she straightens. {{D1}}")

    def __init__(self) -> None:
        self.asks: list[str] = []

    def chat(self, messages, **kw):
        c = messages[-1]["content"]
        self.asks.append(c if isinstance(c, str) else c[0]["text"])
        return self._Reply()

    def json_call(self, messages, schema, **kw):
        c = messages[-1]["content"]
        self.asks.append(c if isinstance(c, str) else c[0]["text"])
        return {
            "shots": [{"beat": "the baker sets the tray down",
                       "camera": {"type": "Push In", "amplitude": "small", "speed": "slow"},
                       "subjects": [], "sync_sound": ["a tray scrapes on stone"]}],
            "ambient_sound": ["a quiet street before sunrise"],
            "style_phrase": "Live-action, cinematic",
            "summary": "The target video shows a baker opening up.",
            "music": "N/A",
        }


def _brief() -> Brief:
    return Brief(intent="A baker sets the first tray on the counter before sunrise.", seconds=8.0,
                 dialogue=[LINE])


def test_the_beat_sheet_call_builds_its_ask_and_names_the_dialogue():
    brief = _brief()
    plan = build_plan(brief, Mode.T2VA, {}, opts=ProfileOptions())
    backend = _Stub()
    sheet = beat_sheet(backend, brief, Mode.T2VA, plan.subjects, {}, plan.shots)
    assert sheet["style_phrase"] == "Live-action, cinematic"
    assert "{{D1}}" in backend.asks[0], "planning sees the protected speech slot"
    assert LINE.text not in backend.asks[0], "literal speech is inserted by code, not retyped by a model"
    assert "{dial}" not in backend.asks[0]


def test_the_shot_body_call_builds_its_ask_and_names_the_placeholder():
    brief = _brief()
    plan = build_plan(brief, Mode.T2VA, {}, opts=ProfileOptions())
    shot = plan.shots[0]
    shot.camera = CameraMove(type="Push In", amplitude="small", speed="slow")
    backend = _Stub()
    body = shot_body(backend, brief, Mode.T2VA, shot, plan.subjects, "Live-action, cinematic")
    assert "{{CAM}}" in body
    ask = backend.asks[0]
    assert "{{D1}}" in ask, "the placeholder for the caller's line has to be described"
    assert "{dial}" not in ask


def test_a_shot_with_no_dialogue_still_builds_its_ask():
    """The branch that says "none", which is the one an f-string bug hides behind."""
    brief = Brief(intent="Rain runs down a shop window.", seconds=8.0)
    plan = build_plan(brief, Mode.T2VA, {}, opts=ProfileOptions())
    backend = _Stub()
    shot_body(backend, brief, Mode.T2VA, plan.shots[0], plan.subjects, "Live-action")
    assert "Dialogue placeholders to place:\nnone" in backend.asks[0]


def test_a_reference_brief_reaches_both_calls_too():
    """The ref2va shape exercises the subject-label branches of both asks."""
    ref = AssetRef(kind=AssetKind.IMAGE, role=Role.SUBJECT, sha256="plate",
                   px=(1024, 576))
    cards = {"plate": AssetCard(sha256="plate", kind=AssetKind.IMAGE, style="Live-action",
                                summary="a black car",
                                subjects=[{"kind": "object", "descriptor": "the black car",
                                           "attributes": ["carbon fibre body"]}])}
    brief = Brief(intent="Put this car on a wet dock road.", seconds=8.0, assets=[ref],
                  dialogue=[LINE])
    plan = build_plan(brief, Mode.REF2VA, cards, opts=ProfileOptions())
    backend = _Stub()
    beat_sheet(backend, brief, Mode.REF2VA, plan.subjects, cards, plan.shots)
    shot_body(backend, brief, Mode.REF2VA, plan.shots[0], plan.subjects, "Live-action")
    assert all("{dial}" not in a for a in backend.asks)
    assert "<Subject 1>" in backend.asks[-1]
