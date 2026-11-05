"""Every declared role has to survive its own renderer. No model and no GPU.

`Role` is the input the whole layer derives from: task types, retention markers and the
subject-definition wording all come off it. Every value is accepted at the API boundary, and
until now the suite exercised most of them only through hand-written documents that a test wrote
and then validated. That proves the RULE and never proves the renderer obeys it, which is how
`role: "sfx"` shipped as a hard 500 on first use: `plan._AUDIO_MARKER` wrote `partially_copy` and
R22 forbids exactly that marker for a role whose definition is "the signal is referenced, not
copied". The deterministic draft is our own output, so an ERROR in it raises rather than falling
back -- the caller got HTTP 500 with an empty body.

So this file renders, and validates what it rendered. Nothing here writes a document by hand.
"""
from __future__ import annotations

import pytest

from h3ir.compile import _assess
from h3ir.draft import deterministic_draft
from dataclasses import replace

from h3ir.models import AssetCard, AssetKind, AssetRef, Brief, Mode, Role, SWAP_ROLES
from h3ir.plan import ProfileOptions, audio_relations, derive_task_types
from h3ir.render import render_ir

# One case per role: the kind that role applies to, and the mode that wiring actually routes to.
# The mode is not a free choice -- an anchor role goes to the keyframe checkpoint and any video or
# audio attachment forces ref2va (mode.py, rule 12.2#1).
ROLE_CASES = [
    (Role.SUBJECT, AssetKind.IMAGE, Mode.REF2VA),
    (Role.ENVIRONMENT, AssetKind.IMAGE, Mode.REF2VA),
    (Role.STYLE, AssetKind.IMAGE, Mode.REF2VA),
    (Role.STRUCTURE, AssetKind.VIDEO, Mode.REF2VA),
    (Role.MOTION_REFERENCE, AssetKind.VIDEO, Mode.REF2VA),
    (Role.STORYBOARD, AssetKind.IMAGE, Mode.REF2VA),
    (Role.FRAME_ANCHOR_FIRST, AssetKind.IMAGE, Mode.I2VA),
    (Role.FRAME_ANCHOR_LAST, AssetKind.IMAGE, Mode.L2VA),
    (Role.EDIT_SOURCE, AssetKind.VIDEO, Mode.REF2VA),
    (Role.CONTINUATION_SOURCE, AssetKind.VIDEO, Mode.REF2VA),
    (Role.VOICE_TIMBRE, AssetKind.AUDIO, Mode.REF2VA),
    (Role.BGM, AssetKind.AUDIO, Mode.REF2VA),
    (Role.MUSIC_STYLE, AssetKind.AUDIO, Mode.REF2VA),
    (Role.BEAT_REFERENCE, AssetKind.AUDIO, Mode.REF2VA),
    (Role.SFX, AssetKind.AUDIO, Mode.REF2VA),
    # Both swap roles ride a picture and are meaningless without a clip to act on, so `_brief_for`
    # attaches the edit source alongside. That is the wiring `check_swap` refuses them without.
    (Role.PLACED_SUBJECT, AssetKind.IMAGE, Mode.REF2VA),
    (Role.REPLACEMENT_SUBJECT, AssetKind.IMAGE, Mode.REF2VA),
]

# The clip a swap role needs beside it. Its card yields exactly one `person` subject, which is what
# makes `plan.bind_replacement` bind: like for like, and unique.
SWAP_CLIP_SHA = "asset-swap-edit-source"
SWAP_CLIP = AssetCard(
    sha256=SWAP_CLIP_SHA, kind=AssetKind.VIDEO, frames_seen=3,
    summary="a driver walks around a car in a tunnel",
    environment="a concrete road tunnel", framing="a medium-wide shot",
    lighting="overhead sodium strip lights", style="Live-action, cinematic",
    motion="the driver crosses frame left to right",
    subjects=[{"kind": "person", "descriptor": "the driver",
               "attributes": ["a grey overall"]},
              {"kind": "object", "descriptor": "the black sports car",
               "attributes": ["carbon fibre body"]}])


def test_every_role_in_the_enum_has_a_case_here():
    """The guard that makes this file stay complete. A role added to the enum and not to the table
    above is a role nothing renders in this suite, which is the state `sfx` was in when it shipped a
    hard 500 on first use."""
    assert {r for r, _, _ in ROLE_CASES} == set(Role), set(Role) - {r for r, _, _ in ROLE_CASES}


def _brief_for(role: Role, kind: AssetKind) -> tuple[Brief, dict[str, AssetCard]]:
    sha = f"asset-{role.value}"
    if kind is AssetKind.IMAGE:
        ref = AssetRef(kind=kind, role=role, sha256=sha, px=(1024, 576),
                       note="the black sports car")
        cards = {sha: AssetCard(
            sha256=sha, kind=kind, style="Live-action, cinematic",
            environment="an empty showroom floor", summary="a black sports car on a showroom floor",
            subjects=[{"kind": "object", "descriptor": "the black sports car",
                       "attributes": ["carbon fibre body"]}])}
    elif kind is AssetKind.VIDEO:
        ref = AssetRef(kind=kind, role=role, sha256=sha, seconds=5.0, frames=120,
                       note="a car rolling through a tunnel")
        cards = {sha: AssetCard(sha256=sha, kind=kind, summary="a car rolling through a tunnel",
                               motion="the car crosses frame left to right", frames_seen=3)}
    else:
        ref = AssetRef(kind=kind, role=role, sha256=sha, seconds=3.0,
                       note="a low engine rumble")
        cards = {sha: AssetCard(sha256=sha, kind=kind, summary="an audio reference.",
                                characterisation="a low engine rumble")}
    refs = [ref]
    if role in SWAP_ROLES:
        refs.append(AssetRef(kind=AssetKind.VIDEO, role=Role.EDIT_SOURCE, sha256=SWAP_CLIP_SHA,
                             seconds=5.0, frames=120, note="the clip to edit"))
        cards[SWAP_CLIP_SHA] = SWAP_CLIP
        # The picture has to define a PERSON for the like-for-like binding to have anything to
        # bind to; the default card above is a car.
        cards[sha] = replace(cards[sha], summary="a driver in a red jacket",
                             subjects=[{"kind": "person", "descriptor": "the driver in red",
                                        "attributes": ["a red jacket"]}])
    brief = Brief(intent="the car pulls forward out of the dark", seconds=5.0, assets=refs)
    return brief, cards


@pytest.mark.parametrize("role,kind,mode", ROLE_CASES, ids=[r.value for r, _, _ in ROLE_CASES])
def test_every_role_renders_a_draft_that_passes_its_own_validator(role, kind, mode):
    """The invariant `compile_brief` raises on: the draft is deterministic, so an ERROR in it is
    our bug and there is nothing to fall back to. Over HTTP that raise is a bare 500.

    One render per role, each validated as rendered. `sfx` failed this on R22-audio-marker-role
    before the marker table was corrected.
    """
    brief, cards = _brief_for(role, kind)
    plan = deterministic_draft(brief, mode, cards, opts=ProfileOptions())
    _, findings, _ = _assess(plan, brief, mode, ProfileOptions(), [])
    errors = [f for f in findings if f.severity == "ERROR"]
    assert not errors, [str(f) for f in errors]


@pytest.mark.parametrize("role,kind,mode", ROLE_CASES, ids=[r.value for r, _, _ in ROLE_CASES])
def test_no_role_writes_its_own_wiring_token_into_the_brief(role, kind, mode):
    """Our own renderer held to the rule the writer is held to (P9). A role name is this layer's
    internal vocabulary and H3 was trained on none of them: the spec's line is "<Audio 1> is the
    voice-timbre reference for <Subject 1> (S1)", hyphenated English. Measured as a real leak on the
    written path once the declared role reached the ask, so the deterministic path needs the guard
    too -- it is the text that ships whenever the writer fails verification.
    """
    from h3ir.plan import ProfileOptions
    from h3ir.render import render_ir
    from h3ir.validate import ROLE_TOKENS_NEVER_IN_PROSE

    brief, cards = _brief_for(role, kind)
    text = render_ir(deterministic_draft(brief, mode, cards, opts=ProfileOptions()),
                     ProfileOptions()).prompt
    leaked = [t for t in ROLE_TOKENS_NEVER_IN_PROSE if t in text]
    assert not leaked, leaked


# ---------------------------------------------------------------- sfx, the specific contradiction

def test_a_sound_effect_reference_is_not_claimed_as_a_copy():
    """`sfx` is defined in this codebase as a reference in three separate places -- analyse.py's
    card summary ("a sound-effect reference"), the retention note plan.py writes for it ("its sound
    texture is referenced") and render.py's definition line ("a sound-texture reference for the
    target video"). The marker table was the only place that called it a copy, and ref-en.txt 4.2
    defines `reference` as exactly "only timbre, rhythm, music style, dialogue content, or sound
    texture is referenced". So the marker was the wrong side of the contradiction, not the rule.
    """
    brief, cards = _brief_for(Role.SFX, AssetKind.AUDIO)
    plan = deterministic_draft(brief, Mode.REF2VA, cards, opts=ProfileOptions())
    assert audio_relations(plan)[0][1] == "reference"
    text = render_ir(plan, ProfileOptions()).prompt
    assert "<Audio 1>: reference - <Audio 1>'s sound texture is referenced." in text


def test_a_referenced_sound_effect_is_not_reported_as_reused_audio():
    """The task-type half of the same contradiction. ref-en.txt 3 splits the two: `audio reuse` is
    "the same audio signal is reused in full or in part", `audio reference` is "the signal is not
    copied directly; only its music style, timbre, dialogue or lyric content, sound-effect texture,
    beat, or continuity is referenced". A retention line saying `reference` under a summary claiming
    reuse is the document contradicting itself, and no rule catches that -- the renderer has to be
    right on its own.
    """
    brief, cards = _brief_for(Role.SFX, AssetKind.AUDIO)
    plan = deterministic_draft(brief, Mode.REF2VA, cards, opts=ProfileOptions())
    assert derive_task_types(plan.manifest, brief) == ["audio reference"]
    assert "[audio reference]" in render_ir(plan, ProfileOptions()).prompt


def test_background_music_still_reports_a_partial_copy():
    """The narrow half. `bgm` legitimately copies part of the signal -- render.py calls it "the
    synchronized audio track ... providing the background music" -- so it keeps `partially_copy`
    and `audio reuse`. A fix that made every audio role a reference would have been the same
    mistake pointing the other way.
    """
    brief, cards = _brief_for(Role.BGM, AssetKind.AUDIO)
    plan = deterministic_draft(brief, Mode.REF2VA, cards, opts=ProfileOptions())
    assert audio_relations(plan)[0][1] == "partially_copy"
    assert derive_task_types(plan.manifest, brief) == ["audio reuse"]


# ---------------------------------------------------------------- storyboard, the same class

def test_a_storyboard_picture_gets_the_line_the_spec_gives_it():
    """Found by the render pass above, not reported: a storyboard-only ref2va brief was a second
    hard 500. A storyboard is a shot-planning anchor rather than a reusable visible unit, so
    `build_subjects` skips it -- correctly -- and nothing else wrote a line for it. That left
    `subject_definitions` empty (S9) with `<Picture 1>` cited nowhere (L4), two ERRORs in the
    deterministic draft, so `compile_brief` raised.

    ref-en.txt 2.2 gives the label its own line and its own wording, which is what is rendered now.
    """
    brief, cards = _brief_for(Role.STORYBOARD, AssetKind.IMAGE)
    plan = deterministic_draft(brief, Mode.REF2VA, cards, opts=ProfileOptions())
    text = render_ir(plan, ProfileOptions()).prompt
    assert "<Picture 1> is a storyboard reference for [Shot 1]" in text
    assert "defining their viewpoint, subject placement, and shot order." in text
    assert "<Picture 1> (storyboard reference): weak_reference -" in text


def test_a_storyboard_picture_is_still_not_a_subject():
    """The reason it had no line is the reason it must not get a <Subject N>: the drawing is not
    content that appears in the video. ref-en.txt 2.2 keeps the two apart, and a storyboard
    promoted to a subject would be a brief asking H3 to render the storyboard.
    """
    brief, cards = _brief_for(Role.STORYBOARD, AssetKind.IMAGE)
    plan = deterministic_draft(brief, Mode.REF2VA, cards, opts=ProfileOptions())
    assert plan.subjects == []
    assert "<Subject 1>" not in render_ir(plan, ProfileOptions()).prompt


def test_a_storyboard_line_still_needs_its_retention_entry():
    """L5 is what makes the standalone line legal, and it stays live: the same definition line
    without a retention entry is still the error the spec describes.
    """
    from h3ir.validate import Context, validate

    text = ("subject_definitions:\n<Picture 1> is a storyboard reference for [Shot 1], defining "
            "its viewpoint, subject placement, and shot order.\n\n"
            "summary:\n[reference generation] The target video follows <Picture 1>.\n\n"
            "retention_analysis:\n<Subject 1> (appears in [Shot 1]): weak_reference - the layout "
            "is followed.\n\ndetailed_description:\nA style line.\n[Shot 1] The camera pushes in "
            "with small amplitude at slow speed on the car.\n\n"
            "overall_soundscape:\nRoom tone continues.\n\nnon_diegetic_music:\nN/A\n")
    found = validate(text, Context(mode="ref2va", n_pictures=1, duration_s=5.167))
    assert "L5-redundant-source-line" in {f.rule for f in found if f.severity == "ERROR"}


def test_the_rule_that_rejects_a_copied_sound_effect_still_bites():
    """The rule stays exactly as strict. If the renderer ever writes a copy marker for `sfx`
    again, R22 fires -- the fix is the renderer obeying the rule, not the rule being relaxed.
    """
    from h3ir.validate import Context, validate

    text = ("subject_definitions:\n<Audio 1> is a sound-texture reference for the target video.\n\n"
            "summary:\n[audio reference] The target video shows a car in a tunnel with <Audio 1> "
            "as its sound-texture reference.\n\n"
            "retention_analysis:\n<Audio 1>: partially_copy - its sound texture is referenced.\n\n"
            "detailed_description:\nA style line.\n[Shot 1] The camera pushes in with small "
            "amplitude at slow speed on the car.\n\noverall_soundscape:\nRoom tone continues.\n\n"
            "non_diegetic_music:\nN/A\n")
    found = validate(text, Context(mode="ref2va", n_audios=1, duration_s=5.167,
                                   declared_roles=(("<Audio 1>", "sfx", ""),)))
    assert "R22-audio-marker-role" in {f.rule for f in found if f.severity == "ERROR"}
