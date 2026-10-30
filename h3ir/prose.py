"""Stage D (first half): the only places a model writes anything.

Two kinds of call, and both are narrow by construction:

  beat_sheet()  one structured call. Decides WHAT happens per shot, the camera from the
                closed enum, which sounds are synchronized vs ambient, the style opening,
                the summary sentence and the music. Every enum is re-validated here --
                a schema on this endpoint constrains shape, not sense.

  shot_body()   one prose call per shot, with that shot's span, beat, subjects and word
                target. Per-shot generation is what makes length arithmetic instead of a
                request, and what makes each shot a different beat instead of a restatement.

Prompt text lives in prompts/*.txt so a change to it is a versioned, scoreable artifact
rather than an edit buried in code. The harness proved that matters: a system prompt with
a page of extra craft rules measurably regressed, and only the validator caught it.
"""
from __future__ import annotations

import functools
import json
import re
from pathlib import Path
from typing import Any

from .backend import Backend, user_message
from .config import get_config
from . import director as _director
from .creativity import ELEMENTS
from .models import AMPLITUDES, AssetCard, Brief, CAMERA_TYPES, Mode, SPEEDS, ShotPlan, SubjectPlan
from .textnorm import normalize
from .language import prompt_policy, protected_intent, restore_dialogue


@functools.lru_cache(maxsize=32)
def load_prompt(name: str) -> str:
    p = get_config().paths.spec_dir / name
    if not p.exists():
        raise FileNotFoundError(f"prompt file not found: {p}")
    return p.read_text(encoding="utf-8").strip()


def beat_schema(n_shots: int) -> dict[str, Any]:
    return {
        "title": "BeatSheet",
        "type": "object",
        "additionalProperties": False,
        "required": ["shots", "ambient_sound", "style_phrase", "summary", "music"],
        "properties": {
            "shots": {
                "type": "array",
                "minItems": n_shots,
                "maxItems": n_shots,
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": ["beat", "camera", "subjects", "sync_sound"],
                    "properties": {
                        "beat": {"type": "string"},
                        "camera": {
                            "type": "object",
                            "additionalProperties": False,
                            "required": ["type"],
                            "properties": {
                                "type": {"type": "string", "enum": list(CAMERA_TYPES)},
                                "amplitude": {"type": "string", "enum": list(AMPLITUDES)},
                                "speed": {"type": "string", "enum": list(SPEEDS)},
                            },
                        },
                        "subjects": {"type": "array", "items": {"type": "string"}},
                        "sync_sound": {"type": "array", "items": {"type": "string"}},
                    },
                },
            },
            "ambient_sound": {"type": "array", "items": {"type": "string"}, "maxItems": 3},
            "style_phrase": {"type": "string"},
            "summary": {"type": "string"},
            "music": {"type": "string"},
        },
    }


def _asset_digest(cards: dict[str, AssetCard], subjects: list[SubjectPlan]) -> str:
    lines = []
    for s in subjects:
        attrs = "; ".join(s.attributes) or "no distinguishing detail recorded"
        lines.append(f"{s.label} = {s.descriptor} ({s.kind}). Features: {attrs}")
    for c in cards.values():
        if c.kind.value == "audio":
            # Facts from the wiring, not a description of sound. Nothing here was heard by a
            # model, so nothing here may be elaborated into one.
            bits = [b for b in (c.summary, c.transcript[:160]) if b]
            if bits:
                lines.append("audio reference (typed facts, do not embellish): "
                             + " | ".join(bits))
    return "\n".join(lines) or "no references attached"


def fix_with_findings(backend: Backend, written: str, findings: list, *,
                      labels: tuple[str, ...] = (), sections: tuple[str, ...] = (),
                      seed: int | None = None, thinking: bool = False,
                      brief: Brief | None = None) -> str:
    """Hand the model its own text plus the findings and ask for a surgical fix.

    The owner's idea, and better than the code repairers it replaces: a bespoke editor per failure
    mode is a small parser with its own bugs that only handles the shapes someone anticipated,
    while the model already holds the intent behind the text. Validator findings happen to be
    ideal review feedback -- they name the rule, the offence and the offending line.

    The spec is NOT resent. It is already in the system prompt of the composing call, and
    repeating it invites a wholesale rewrite instead of a surgical fix.

    The LABEL INVENTORY is resent, and that distinction is the whole point. The first eval run of
    this loop fell back on three of six briefs, every time on `L2-undefined-subject` /
    `L3-phantom-media` surviving both rounds -- the model had invented a `<Subject 3>` where two
    exist. Those are BINDING faults, and the fix call was being asked to repair a binding with the
    binding table removed: "<Subject 3> is used but never defined" can be satisfied by deleting the
    reference or by inventing a definition for it, and nothing in the ask said which labels are real.
    The inventory is not the specification -- it is a fact about this request's wiring, three lines
    the composing call already hands over, and withholding it was an accident of where the split fell
    rather than a decision.
    """
    protected = {}
    if brief is not None:
        def hide(m):
            token = '{{UTTERANCE%d}}' % (len(protected)+1)
            protected[token] = m.group()
            return token
        written = re.sub(r'<d>.*?</d>', hide, written, flags=re.S)
    numbered = "\n".join(f"{i}. [{f.rule}] {f.msg}" for i, f in enumerate(findings, 1))
    if brief is not None:
        for i,d in enumerate(brief.dialogue,1):numbered=numbered.replace(d.text, '{{D%d}}'%i)
    inventory = ""
    if labels:
        inventory = (
            "\nThese are the ONLY labels that exist for this request. Every one of them is real and "
            "no others are — a label outside this list references nothing at all, so the fix for an "
            "unknown label is to remove it or replace it with one of these, never to define it:\n"
            + ", ".join(labels) + "\n")
    ask = (
        "Your brief failed mechanical verification. Below are the exact findings, then your text.\n\n"
        f"FINDINGS\n{numbered}\n"
        f"{inventory}\n"
        "Fix every finding and change nothing else. "
        + ("" if any(f.rule == "T11-shot-count-pinned" for f in findings) else "Keep your shots, ")
        + "Keep your camera moves, your "
        "performance beats, your wording. Do not rewrite, do not shorten, do not re-plan — this is "
        "a correction pass, not a second draft. Output the corrected "
        + (f"{len(sections)} sections ({', '.join(sections)})" if sections else "brief in full")
        + " and nothing else.\n\n"
        f"YOUR TEXT\n{written}"
    )
    reply = backend.chat(
        [{"role": "system", "content": "You correct mechanical faults in your own H3 brief. You "
                                       "make the smallest edit that satisfies each finding." +
                                       (f" Keep descriptive prose in {brief.description_language}; preserve every {{{{UTTERANCE}}}} / {{{{D}}}} placeholder exactly." if brief else "")},
         {"role": "user", "content": ask}],
        thinking=thinking, temperature=0.2, seed=seed, max_tokens=20000)
    text=reply.content
    for token,original in protected.items():text=text.replace(token,original)
    return restore_dialogue(text,brief) if brief is not None else text


def _reference_governed(licence) -> list[str]:
    """The attributes to name as reference-governed in the ask.

    **The sentence states the RULE, not a claim about the request.** It used to read "The request is
    silent about {attrs}", which on a detector miss is a false statement about the caller's own
    request, handed to the model beside the request text -- a contradiction, and the wrong resolution
    of it is the drift the policy exists to stop. §35's line is "absence of a statement is not a
    statement of absence"; asserting silence is that mistake made positively. Conditional wording is
    true under a hit and under a miss, so a miss degrades to a weaker hint and the one reader holding
    both the request and the reference description does the disambiguating.

    The MEDIUM is excluded because it already has a better channel: the style block states it with
    the bare-adjective rule attached ("the request did not ask to change the medium, so keep it").
    Listing it here as well would tell the model the request governs the medium wherever the request
    says "anime", which is precisely what the owner's rule refuses.
    """
    from .licence import MEDIUM
    return [a for a, who in licence.governs.items() if who == "reference" and a != MEDIUM]


# What each reference-only audio role means: the name to CALL it in the brief, and the property it
# supplies. Keyed by the role's own value so the ask, `plan._AUDIO_MARKER` and the validator's rules
# all read the same vocabulary.
#
# The name is the spec's own hyphenated phrase and the role's token appears nowhere in this ask, for
# a reason measured on the first live run of this fact: told the role was `beat_reference`, the writer
# wrote "<Audio 1> is the beat_reference for the target video" into `subject_definitions`. A
# snake_case wiring token is prose H3 was never trained on, and ref-en.txt 2.4's own line is
# "<Audio 1> is the voice-timbre reference for <Subject 1> (S1)". P9 catches it if it happens anyway.
_AUDIO_PROPERTY = {
    "voice_timbre": ("a voice-timbre reference", "voice timbre and delivery"),
    "sfx": ("a sound-texture reference", "sound texture"),
    "music_style": ("a music-style reference",
                    "musical style, instrumentation and tempo, and the score you write for the "
                    "target video is new music in that style"),
    "beat_reference": ("a beat reference",
                       "beat and tempo, which set the timing of the cuts and of the action"),
}


def audio_task_facts(labels: tuple[str, ...], task_types: tuple[str, ...],
                     audio_roles: tuple[tuple[str, str], ...] = ()) -> str:
    """What the wiring settles about the two audio task types, stated as a fact in the ask.

    The renderer already owns this: "a prose stage that could write the prefix could invent a
    relationship the pack does not contain" (render.render_summary). The write-first inversion
    handed the prefix back to the model without handing over the fact it needed, and the model
    then claimed `audio reuse` on 6 of 7 video edits -- reasonably, because keeping the original
    audio is what editing a video means. It is still a claim the render cannot deliver: a
    soundtrack is a separately wired input, and with none wired the signal never reaches the model.

    The audio half only is pinned, because the wiring decides it completely. Whether reusing a
    person out of a reference video also counts as `reference generation` is a judgement about how
    the reference is used, and ref-en.txt 3 phrases it as one ("normally", "only when"), so the
    model keeps it.

    `audio_roles` adds the per-label half. The aggregate sentence names the relationship the roles
    declare in total, which a mixed brief -- one track copied, another referenced for its style --
    cannot express, and which measured as insufficient even on single-asset briefs. Roles with no
    entry in `_AUDIO_PROPERTY` get no sentence, which is what every caller predating the parameter
    passes.
    """
    audio = [lb for lb in labels if lb.startswith("<Audio")]
    if not audio:
        return ("No <Audio N> is attached, so the task-type prefix may NOT contain `audio reuse` "
                "or `audio reference`: both claim a relationship to an attached audio signal and "
                "there is none. If the request implies the source video's own sound, note that a "
                "video's soundtrack is a separately wired input and none is wired here, so this "
                "render GENERATES the target video's audio. Do not write that the original "
                "audio is preserved, reused or carried over anywhere in the brief; decide what the "
                "video should sound like and put it in overall_soundscape.")
    declared = [t for t in task_types if t.startswith("audio ")]
    out = [f"Attached audio: {', '.join(audio)}. The audio relationship their roles declare is "
           f"{' + '.join(declared) or 'reference generation only'}, and that is the audio task "
           "type to use; do not claim the other one. The retention marker has to agree with it: "
           "`fully_copy` and `partially_copy` are copies, `reference` and `weak_reference` are "
           "not, and a line that says one while the prefix says the other contradicts itself."]
    # Per label, for the roles whose definition IS "a property is referenced, not the signal". The
    # aggregate sentence above is not enough on a mixed brief and was not enough on a single one
    # either: `S6-beat-rhythm` wrote "<Audio 1>: fully_copy - <Audio 1> is reused 1:1 as the target
    # video's complete final audio track" in 5 of 5 runs, and `X9`, whose request says outright that
    # nothing from the recording is used, in 5 of 5. The role is the caller's statement of what the
    # recording is FOR, so it is stated as a fact here for the same reason `video_task_facts` states
    # the declared video role: the writer was reading the intent and answering the question it
    # seemed to ask.
    for label, role in audio_roles:
        named = _AUDIO_PROPERTY.get(role)
        if not named:
            continue
        name, prop = named
        out.append(
            f"{label} is attached as {name}, so only its {prop}. Its signal is NOT used in the "
            f"target video. Its retention marker is `reference`, or `weak_reference` if the request "
            f"asks for no more than a broad likeness, and never `fully_copy` or `partially_copy`; "
            f"and nowhere in the brief — not in the summary, the definitions, the description or "
            f"the music section — may {label} be described as reused, copied, played, or serving "
            f"as the target video's audio track. Define it the way the specification does, in plain "
            f"words: `{label} is {name} for the target video`, and then say what it supplies. "
            f"However the request is phrased, the caller has said what that recording is for.")
    # And the copy side's own distinction, in ref-en.txt 4.2's words. `bgm` legitimately copies and
    # nothing here legislates which of the two copy markers it takes -- only the request decides,
    # which is why `X4` is right to write `fully_copy` and `X10` right to write `partially_copy` for
    # the same role. What the writer was missing is the marker table's definition: `S2-copy-part`,
    # whose request lays other sound over the top of part of the track, claimed `fully_copy` and
    # "reused 1:1 as the target video's complete final audio track" in 5 of 5 runs.
    if any(role == "bgm" for _lb, role in audio_roles):
        out.append("`fully_copy` says the complete source audio is the target video's complete "
                   "final audio track, with nothing added, removed or laid over it. If the request "
                   "keeps only part of the timeline, or adds, removes or replaces any sound after "
                   "copying, the marker is `partially_copy`.")
    return "\n".join(out)


def video_task_facts(video_roles: tuple[tuple[str, str], ...],
                     task_types: tuple[str, ...]) -> str:
    """What a declared video role settles about the task-type prefix, stated as a fact in the ask.

    The exact counterpart of `audio_task_facts`, for the half that had nothing. That function exists
    because the writer claimed `audio reuse` on 6 of 7 video edits and the wiring, not the model,
    knows the answer; the video half went the other way and was just as reproducible. A clip attached
    as `edit_source` with the intent "the same volley, but the stadium is empty and it is raining
    hard" came back `[reference generation]` in 6 of 7 seeds, with the clip decomposed into four
    subjects and no editing opening sentence, while the same role with "change the car in this clip to
    a white one" was right 7 of 7. Nothing in the ask mentioned the role, so the writer read the
    intent and answered the question it seemed to ask.

    Only the two roles the wiring settles completely are pinned. `style`, `subject`, `environment` and
    `storyboard` on a video stay the model's call, because ref-en.txt 3 phrases that as a judgement:
    "If a reference video provides only camera movement, cuts, or rhythm, it normally belongs to
    reference generation." So does `reference generation` alongside an edit, which is legal and often
    right when the clip's people are reused as well.
    """
    pinned = {"edit_source": "video editing", "continuation_source": "video continuation"}
    lines = [(label, pinned[role]) for label, role in video_roles if role in pinned]
    if not lines:
        return ""
    out = []
    for label, want in lines:
        out.append(f"{label} is attached with the declared role for `{want}`, so the task-type "
                   f"prefix MUST contain `{want}`, however the request is phrased. The caller has "
                   f"said what that clip is for; a request to change the setting, the weather, the "
                   f"colour or anything else in it is still an edit of {label}, not a new video "
                   f"built out of its contents.")
        if want == "video editing":
            out.append(f"Begin the summary immediately after the prefix with: The target video is "
                       f"an edited version of {label}. Define the clip as: {label} is the source "
                       f"video for the target video edit.")
        else:
            out.append(f"Say in the summary what the new content continues from, and define the "
                       f"clip as: {label} is the source video the target video continues from.")
    extra = [t for t in task_types if t == "reference generation"]
    if extra:
        out.append("Keep `reference generation` in the prefix as well if the clip's subjects are "
                   "also being reused as content; the two combine with ` + `.")
    return "\n".join(out)


def base_mode_label_facts(subjects: list[SubjectPlan], labels: tuple[str, ...],
                          picture_roles: tuple[tuple[str, str], ...]) -> str:
    """What the attached pictures ARE, for the three-section formats.

    A base mode has no `subject_definitions` section, so the facts cannot be definition lines.
    Handing `<Subject 1> is the black car in <Picture 1>` to this format hands the model a sentence
    with nowhere to live: it wrote the label, no section defined it, and L2 rejected the brief. i2va
    degraded on exactly that pair 3 times in 7 -- `<Subject 1>` used and defined inline, and
    `<Picture 1>` never cited at all, so L4 fired too.

    The system prompt for these modes already says subject labels do not exist here. The ask was
    contradicting it with a fact sheet in the other format's shape, and a fact sheet that looks like
    output gets used as output.
    """
    role_says = {
        "frame_anchor_first": "IS the target video's first frame, at 0.00 seconds, and belongs to "
                              "[Shot 1]",
        "frame_anchor_last": "IS the target video's last frame and belongs to the final shot; it "
                             "does not belong to [Shot 1]",
    }
    by_label = dict(picture_roles)
    lines: list[str] = []
    for label in labels:
        shows = [s for s in subjects if label in s.sources]
        what = "; ".join(s.descriptor + (f" ({', '.join(s.attributes[:6])})" if s.attributes else "")
                         for s in shows)
        line = f"{label} {role_says.get(by_label.get(label, ''), 'is a reference for this video')}"
        lines.append(line + (f". It shows {what}." if what else "."))
    return "\n".join(lines) + (
        "\n\nThese are the only labels this request has. This format has NO subject_definitions "
        "section, so `<Subject N>` does not exist in it and writing one is a defect rather than a "
        "style choice: name people, objects and places in plain prose, the same way every time they "
        "appear. Cite each picture above by its label in the shot it belongs to: an attached "
        "picture the brief never names is still wired into the render and still costs rows on every "
        "sampling step.")


def storyboard_facts(picture_roles: tuple[tuple[str, str], ...]) -> str:
    """What a declared storyboard role settles, stated as a fact in the ask.

    The same disease audio_task_facts and video_task_facts cure, found the same way: a picture
    attached with the declared role `storyboard` came back defined as `<Subject 2> ... the modern
    showroom environment in <Picture 2>`, shipped ready, at the first seed tried and again
    service-direct. `build_subjects` refuses to make a subject of a storyboard, so the label had no
    definition line, fell into the "attached and NOT yet described" block, and that block's
    instruction is to define it. Nothing said what it was, so the writer read the pixels and
    answered: scenery.

    ref-en.txt 2.2 gives the construct: a standalone `<Picture N>` line stating which shots it maps
    to and what planning information it provides. The deterministic draft has written that form all
    along (render.py); this states it where the shipped document is actually written.
    """
    boards = [label for label, role in picture_roles if role == "storyboard"]
    if not boards:
        return ""
    lines = []
    for label in boards:
        lines.append(
            f"{label} is a STORYBOARD, declared by the caller: a shot-planning sketch. It never "
            f"appears in the target video. Give {label} its own standalone definition line in "
            "subject_definitions stating which shots it maps to and what planning information it "
            "provides (viewpoint, subject placement, shot order), in the form '<Picture N> is a "
            "storyboard reference for [Shot 1] and [Shot 2], defining ...'. Never define a "
            f"<Subject N> from {label}, never describe its content as part of any scene, and in "
            f"retention_analysis give it one line as '{label} (storyboard reference): "
            "weak_reference - the viewpoint, subject placement and shot order are followed, while "
            "the drawing itself is not reproduced.'")
    return "\n".join(lines)


def style_facts(picture_roles: tuple[tuple[str, str], ...]) -> str:
    """What a style-role plate is, stated where the document is written.

    The storyboard role needed this exact statement (measured: the board became scenery) and got it;
    style had the same defect unfixed. Measured on the tray surface: a line drawing declared
    `style`, whose note said its gnome must stay out, came back with the gnome as Subject 1,
    fully_preserved, in the scene. The role travels in the manifest; nothing told the writer what it
    means. R29-style-cited-as-content is the deterministic check behind this statement.
    """
    plates = [label for label, role in picture_roles if role == "style"]
    if not plates:
        return ""
    lines = []
    for label in plates:
        lines.append(
            f"{label} is a STYLE REFERENCE, declared by the caller: it lends its look — medium, "
            f"line, palette, shading, composition — and nothing else. Its contents never appear in "
            f"the target video. Give {label} one standalone line in subject_definitions in the "
            f"form '{label} is the style and composition reference for the target video, defining "
            f"...', name the aesthetic in detailed_description, and in retention_analysis give it "
            f"one line as '{label} (style and composition): ...'. Never define a <Subject N> from "
            f"{label} and never place anything drawn or photographed in it into any shot.")
    return "\n".join(lines)


def structure_facts(video_roles: tuple[tuple[str, str], ...]) -> str:
    """What a structure-role clip is, stated where the document is written.

    The style role's statement, one asset-kind over. Measured (matrix row 26): asked for "the
    camera movement and the cutting rhythm and nothing else", the writer adopted the structure and
    still walked the clip's man, crowd and glowing sphere into the new scene. The role travels in
    the manifest; nothing told the writer what it means. R30-structure-cited-as-content is the
    deterministic check behind this statement.
    """
    clips = [label for label, role in video_roles if role == "structure"]
    if not clips:
        return ""
    lines = []
    for label in clips:
        lines.append(
            f"{label} is a STRUCTURE REFERENCE, declared by the caller: it lends how it is shot "
            f"and cut — camera movement, framing rhythm, cut timing — and nothing else. Its "
            f"contents never appear in the target video. Give {label} one standalone line in "
            f"subject_definitions in the form '{label} is the source video providing the camera "
            f"movement and cutting rhythm for the target video.', follow its moves and cuts in "
            f"detailed_description, and in retention_analysis give it one line as '{label} "
            f"(camera movement and cutting rhythm): ...'. Never define a <Subject N> from "
            f"{label} and never place anything seen in it into any shot.")
    return "\n".join(lines)


def edit_source_facts(video_worlds: tuple[tuple[str, str, str], ...],
                      picture_roles: tuple[tuple[str, str], ...] = ()) -> str:
    """What a video EDIT settles about the target video, stated where the document is written.

    The measured defect, and it is the largest one in this file's history. Four seeds of "replace
    the man in this clip with the elderly woman in the picture: everything else stays exactly the
    same" put the target video in the PLATE's grey studio -- 4 of 4 -- and killed the camera move
    in 3 of 4, against a source clip shot in a carpentry workshop with a slow push in.

    Two causes, and this block answers both.

    The clip's world was never a fact the writer had. `_definition_lines` walks `subjects`, and a
    video card's subjects are its people and props: the man, the hammer, the plank. Its
    `environment`, `framing`, `lighting` and `motion` reach nothing. So the only setting anybody
    described to the writer was the plate's, and the plate is a studio portrait.

    And nothing said that a picture's own backdrop is not the target's. ref-en.txt 2.2: "If an
    image is used only to define a character, scene, costume, or style, do not create a standalone
    picture entry" -- the image supplies the character, not the room it was photographed in.

    Scoped to `edit_source` on purpose. A continuation's target is NOT the source video, so
    "everything the request does not change stays as the clip has it" is false there, and no
    measurement of that case exists yet. That is the extension point, not an oversight.
    """
    if not video_worlds:
        return ""
    lines = []
    for label, world, camera in video_worlds:
        lines.append(
            f"{label} is the video being EDITED, so the target video IS {label} with the "
            f"requested change made to it. Everything the request does not ask to change stays "
            f"exactly as {label} has it: the setting, the framing, the camera movement, the "
            f"lighting, the cutting and the timing of every action. Write detailed_description as "
            f"{label} playing with that change applied, not as a new scene assembled out of its "
            f"parts, and never relocate it.")
        if world:
            lines.append(f"What {label} shows, observed from its frames: {world}")
        # The camera half is stated separately, and its ABSENCE is stated too. Silence about the
        # camera measured as "static camera" in 3 of 4 seeds on a source that pushes in slowly:
        # a description with nothing to say about the camera says it holds still, and that is an
        # assertion contradicting the very clip the writer was told to preserve. So either the
        # observation is handed over, or the instruction is to write no camera sentence at all --
        # which the format allows: ref-en.txt 5.1 asks for movement type, amplitude and speed
        # "when they need to be expressed", and on an unchanged camera they do not.
        if camera:
            lines.append(f"What the camera does in {label}, observed from its frames: {camera}. "
                         f"The target video's camera does exactly that and nothing else.")
        else:
            lines.append(
                f"The camera movement of {label} was NOT observed, so you do not know what it is. "
                f"Write no camera sentence at all for this edit, and in particular do not write "
                f"that the camera is static, holds, or does not move — that is a claim about "
                f"{label} that nothing here has checked, and it contradicts the clip if it moves. "
                f"{label} is wired into the render and carries its own camera with it, so the "
                f"move survives whether or not the text names it.")
    if any(lb.startswith("<Picture") for lb, _ in picture_roles):
        lines.append(
            "An attached picture supplies only the thing it defines — a person, an object, a "
            "look. The backdrop, the lighting and the framing of that photograph belong to the "
            "photograph and are not the target video's. Whatever a picture happens to have been "
            "shot against, the target video stays in the edited clip's own setting, under its own "
            "light, in its own framing.")
    return "\n".join(lines)


def swap_facts(picture_roles: tuple[tuple[str, str], ...],
               video_roles: tuple[tuple[str, str], ...],
               taken_over: tuple[tuple[str, str], ...] = ()) -> str:
    """What `placed_subject` and `replacement_subject` mean, per label.

    `taken_over` is ((picture_label, taken_subject_label), ...) as the planner bound it: which
    figure in the clip each replacement picture stands in for. It is keyed on the picture because
    that is the label carrying the role. It is handed over rather than described, because
    choosing WHICH figure is structure and structure is not the model's to decide (rule 1); the
    planner binds like for like and refuses at intake when that is not unique.
    """
    clip = next((lb for lb, role in video_roles if role == "edit_source"), "")
    placed = [lb for lb, role in picture_roles if role == "placed_subject"]
    replacing = [lb for lb, role in picture_roles if role == "replacement_subject"]
    if not clip or not (placed or replacing):
        return ""
    taken = dict(taken_over)
    lines = []
    for label in placed:
        lines.append(
            f"{label} is attached as a PLACEMENT, declared by the caller: the person or object it "
            f"shows is ADDED into {clip}, and nothing already in {clip} is removed or replaced. "
            f"Define it as an ordinary <Subject N> citing {label}, say in that definition that it "
            f"is placed into {clip}, and in detailed_description describe where in the frame it "
            f"enters and what it does there while everything else in the clip carries on unchanged.")
    for label in replacing:
        # The bound label is a <Subject N> the planner created from the clip's own card, so it is
        # already defined in the facts above; naming it here is what turns "someone is replaced"
        # into a statement about one identifiable figure.
        who = taken.get(label, "")
        target = f" It takes the place of {who}" if who else ""
        lines.append(
            f"{label} is attached as a REPLACEMENT, declared by the caller: the person or object "
            f"it shows takes over from a figure already in {clip}.{target}, and everything else "
            f"about {clip} is untouched — the same camera movement, the same framing, the same "
            f"actions at the same moments, the same words spoken at the same moments, the same "
            f"setting and the same light."
            + (f" What survives of {who} is its position in frame, its actions and its timing; "
               f"its appearance does not, because {label} is now the one performing them. In "
               f"retention_analysis give {who} the marker `attribute_transfer` — the "
               f"specification's own word for characteristics transferred to a different "
               f"identifiable target subject — and never `fully_preserved`, which would claim the "
               f"figure the caller asked to swap out is still there."
               f" Define {who} with NO appearance detail: name it only well enough to find it in "
               f"{clip}, and say in the same line that it does not appear in the target video. "
               f"subject_definitions is conditioning like every other section, so a list of "
               f"{who}'s hair, build and clothing is a description of somebody to draw — measured, "
               f"that put the figure the caller removed back on screen for the opening frames."
               if who else ""))
    return "\n".join(lines)


def reference_picture_facts() -> str:
    """The one thing a full-reference brief must not say about its pictures.

    ref2va conditions on a picture as content: what it shows is redrawn into the requested scene,
    and there is no mechanism that pins an exact frame -- which is why an anchor role arriving on
    this route is downgraded to a subject reference with a finding (compile.X10). The system prompt
    is the spec, and the spec teaches the standalone `<Picture N>` line for a picture that IS a
    frame, so the model wrote "is the first frame of [Shot 1]" on a single-image ref2va brief 3
    times in 7 and R10 rejected the whole brief. Nothing had told it which case it was in.

    UNCONDITIONAL, and that is the correction rather than a simplification. This used to open with
    `if any(role.startswith("frame_anchor") ...): return ""`, to avoid asserting "none of these is a
    frame" over a picture that really was one. On this route none ever is: the compiler rewrites every
    anchor role to `subject` before the plan is built (compile.py, the X10 branch) and the roles this
    was handed came off that plan's manifest, so the guard read post-downgrade data and could not
    fire. It was covered by a test that hand-wrote an anchor role and asserted the "" -- which is how
    unreachable code stays green. The honest statement is the one that matches the wiring.
    """
    return ("None of the pictures here is a frame of the target video. This checkpoint takes them "
            "as content references, meaning what they show is redrawn into the scene the request "
            "asks for, and it has no exact-frame mechanism. So never write that a picture `is the "
            "first frame of` or `is the last frame of` a shot, and never claim `keyframe "
            "completion`: both "
            "promise an exactness this render cannot deliver. Cite the picture inside the definition "
            "of what it shows, or as the composition, storyboard or style anchor it is.")


# ref-en.txt 5.2's word range, stated in the ask rather than left inside the copied specification.
#
# Measured over 103 ready, written, non-editing ref2va documents: 3 in the 350-500 band, median 218,
# minimum 74. `P2-too-short` fired on 90 of them and stays a WARN for good reasons that are recorded
# beside it, but a warning nobody acts on is not pressure and this is not an argument about the last
# 50 words. The second half matters as much as the first: a length target with no answer to "from
# what" gets padding, and the spec's own answer is observation -- composition, appearance, position,
# environment, lighting, state changes, camera, sound.
LENGTH_TARGET = (
    "Length: `detailed_description` is 350 to 500 words for a generation task like this one. Aim for "
    "400 and divide them across however many shots you choose, so two shots is about 200 words each. "
    "Reach it by describing what is actually in the frame: surfaces, materials, the ground, the "
    "light and its direction, what each hand is doing, where each subject sits in the frame, and the "
    "direction of every movement. Do not pad, do not restate the reference relationships, and do not "
    "summarise the plot; a short brief and an inflated one fail the same way, by telling the model "
    "less than it needs about the picture it has to make.")


# ref-en.txt 2.1's other half, which the definition lines cannot express.
#
# `plan.build_subjects` walks the manifest and builds every subject with `sources=[e.label]`, one
# entry per attached file, and those lines are then handed over as facts to use or reword. So the
# writer rewords two lines into two lines, and the construct "One subject may be defined by multiple
# reference assets" appeared in 0 of 11 shipped documents. Asked in as many words to define one
# subject drawing on two pictures, the writer produced the merged form correctly and the document was
# then lost for unrelated reasons.
#
# Which two assets show one subject is a judgement about identity that nothing in this layer can make
# without being told: the analyser sees each file alone, and merging on a descriptor match would be
# inventing a fact about the caller's own material. The request is where that fact lives, and the
# writer is the only stage that reads it. So the lines stay one per asset and the licence is stated.
MERGE_SUBJECTS = (
    "Those lines are one per attached file. If the request says one subject draws on SEVERAL of "
    "them, combine them into a single definition that states what each asset provides, in this "
    "form: `<Subject 1> is the woman whose appearance comes from <Picture 1> and whose walking "
    "motion comes from <Video 1>.` Then use that one label everywhere. Never define the same person "
    "or object twice under two labels: two labels for one subject split the identity the render is "
    "supposed to hold, and the second one usually ends up in no shot at all. One asset can also "
    "provide several subjects, which is the same rule read the other way.")


# What a brief with no image attached has to be told, because the specification in its system prompt
# teaches a construct it cannot use. Four of fifty recorded audio-only briefs asserted that the wav
# was a frame of the video, and seven claimed `keyframe completion` with nothing visual attached.
NO_PICTURES_AT_ALL = (
    "No image is attached to this request. Nothing in this brief is a frame of the target video, so "
    "never write that anything `is the first frame of` or `is the last frame of` a shot, never give "
    "an <Audio N> or a <Video N> a frame parenthetical, and never claim `keyframe completion`: that "
    "task type is for an image serving as a concrete frame and there is no image here. An <Audio N> "
    "is a sound signal; it has no frames and it is not an image.")


def reference_audio_words(transcripts: tuple[tuple[str, str], ...]) -> str:
    """The words on an attached recording, handed to the stage that writes the document.

    They reached `plan_shots` and `beat_sheet` through `_asset_digest` and stopped there. The call
    that writes every shipped section builds its facts from the subject definition lines, which walk
    `subjects` and never touch a card's transcript, so an audio-only brief fell into the `unnamed`
    branch and was told to "say only what its note above states". Asked in as many words to have a
    line reperformed verbatim, the compiler produced no `<d>` block at all, 7 times out of 7.

    Both halves of ref-en.txt 5.4 travel with the words, because handing them over without the
    prohibition would trade the defect for its mirror image: a timbre-only reference whose original
    dialogue gets carried into a video that was never supposed to contain it. Which case this is
    depends on the request, and the writer is the only stage that reads the request.
    """
    lines = [f"{label}: {t.strip()}" for label, t in transcripts if (t or "").strip()]
    if not lines:
        return ""
    return ("Words the caller's speech recogniser transcribed from the attached audio, verbatim:\n"
            + "\n".join(lines)
            + "\nThese are the words the recording contains. If the request asks for them to be "
              "reperformed, or if the audio's own signal is reused so they are audible in the "
              "render, they must appear inside <d> exactly as written above, in their original "
              "language, attributed to whoever the request says speaks them. If only the timbre, "
              "rhythm, emotion or delivery of that audio is being referenced, do not carry these "
              "words into the target video at all. Nothing else about the recording is known: you "
              "have not heard it, so do not describe how it sounds beyond the note supplied for it.\n"
              "Write them in exactly ONE place, inside <d>, and do not quote them in the prose "
              "around it. A double-quoted span in this format is text burned into the frame, so "
              "quoting the line makes the render letter it across the picture as well as speak it.")


# base-en.txt 4.4's two continuity markers, stated beside the lines the writer is placing.
#
# Both existed only as one sentence of spec text inside a long system prompt, and neither reached a
# natural request: `<scenetrans>` appeared in 0 of 7 runs whose request said the sentence must keep
# running across the cut, and `<cutoff>` in 0 of 7 whose request said the video ends mid-sentence.
# Asked for them explicitly the writer produced both, so the construct was reachable and unprompted.
#
# The first sentence is the one that matters most. Told to mark the join, the writer put the WHOLE
# line inside <d> on both sides of the cut in 7 of 7 runs, which instructs H3 to say it twice. This
# states the format's rule and invents nothing about the request: the caller supplied the line once.
DIALOGUE_PLACEMENT = (
    "Each line above is spoken ONCE in the target video, and every word of it appears in exactly one "
    "<d> block. If a line is still running when you cut, divide it at the cut: the words before the "
    "cut close one <d> block, the words after it open the next one, no word appears in both, and both "
    "connecting points carry <scenetrans> with a sentence saying the audio continues across the cut. "
    "Do not write the whole line and then repeat part of it after the cut, and do not stand an "
    "ellipsis in for the half you left out. Repeated words are repeated speech: the model says them "
    "again. If a line is still being spoken when the render ends, stop it where the video stops and "
    "mark that with <cutoff>.")


# The director block, appended AFTER the creativity dial at every site that takes one. The order is
# not cosmetic: the dial states its prohibitions absolutely ("no setting overrides that"), and the
# profile has to read as filling what is left rather than as competing with it. Reversed, the
# strongest sentence in the ask would be a taste and the absolute one would trail it.
def director_block(director, *, scope=None) -> str:
    """What the profile tells the writer, or "" when there is no profile.

    `scope` is passed so the block can say out loud that a score is already ruled out. That is the
    one place the two controls touch. A profile is the caller's own prose and is never edited, so
    the resolution is a SENTENCE rather than a silence: the writer is told the decision is made
    before it reads a description of music that cannot exist.
    """
    if director is None:
        return ""
    from .creativity import SCORE
    scored = scope is None or scope.permits(SCORE)
    return _director.brief_instruction(director, scored=scored)


def compose_brief(backend: Backend, brief: Brief, subjects: list[SubjectPlan],
                  cards: dict[str, AssetCard], target, labels: tuple[str, ...], *,
                  prompt_name: str = "compose.v2.txt", seed: int | None = None,
                  thinking: bool | None = None, images: list[str] | None = None,
                  style=None, licence=None, scope=None,
                  director=None,
                  mode: Mode | None = None,
                  task_types: tuple[str, ...] = (),
                  picture_roles: tuple[tuple[str, str], ...] = (),
                  video_roles: tuple[tuple[str, str], ...] = (),
                  audio_roles: tuple[tuple[str, str], ...] = (),
                  audio_transcripts: tuple[tuple[str, str], ...] = (),
                  # (label, what the clip shows, what its camera does) for every video attached
                  # as `edit_source`, and (picture label, the subject it takes over from) for every
                  # bound replacement. Pre-extracted like `audio_transcripts` and `picture_roles`,
                  # so this module never has to join a manifest label to a card hash.
                  video_worlds: tuple[tuple[str, str, str], ...] = (),
                  taken_over: tuple[tuple[str, str], ...] = (),
                  generation_task: bool = True,
                  omit: tuple[str, ...] = ()) -> str:
    """One call. The model writes all six sections and decides everything creative in them.

    The facts are handed over and the craft is not. Labels, duration and dialogue are stated as
    givens; cuts, camera, performance and the sound shape are the model's. Shot count is the
    model's exactly while `shots` is unset: an explicit count is the caller's contract, stated in
    the ask and enforced by T11-shot-count-pinned.
    """
    # `None` keeps the six-section shape, which is what every caller predating the split passes.
    is_ref = mode is None or mode is Mode.REF2VA
    if not is_ref:
        label_facts = base_mode_label_facts(subjects, labels, picture_roles) if labels else ""
    else:
        # Handed over as the definition LINES, in the exact form the spec wants. An earlier version
        # listed them as "LABEL = description" and the model copied that shape straight into
        # subject_definitions, which then failed the "<Subject N> is ..." rule. A fact sheet that
        # looks like output gets used as output.
        label_facts = "\n".join(_definition_lines(subjects, cards, labels))
        if label_facts and len({s for x in subjects for s in x.sources}) > 1:
            label_facts += "\n" + MERGE_SUBJECTS
        # Every label in the wiring, not only the subject ones. `_definition_lines` walks `subjects`,
        # so an <Audio N> or a bare <Video N> was never named in the ask at all -- and a model cannot
        # bind a label it was not told exists. A video-plus-audio brief came back with the audio
        # referenced nowhere, which is an asset the app would wire in against text that never
        # mentions it.
        board_facts = storyboard_facts(picture_roles)
        if board_facts:
            label_facts += ("\n" if label_facts else "") + board_facts
        look_facts = style_facts(picture_roles)
        if look_facts:
            label_facts += ("\n" if label_facts else "") + look_facts
        shape_facts = structure_facts(video_roles)
        if shape_facts:
            label_facts += ("\n" if label_facts else "") + shape_facts
        unnamed = [lb for lb in labels if lb not in label_facts]
        if unnamed:
            label_facts += ("\n" if label_facts else "") + (
                "Also attached and NOT yet described above: " + ", ".join(unnamed) + ". Every one of these "
                "is wired into the render and must be referenced in the brief — define what it is, and "
                "give it a retention line. An attached reference the text never mentions is dead weight "
                "the model still pays for.\n"
                "For any <Audio N> in that list: you have NOT heard it and cannot describe what it "
                "contains. Say only what its note above states, and never claim it came from a <Video N> "
                "— the wiring decides that and you have not been told it.")
        if any(lb.startswith("<Picture") for lb in labels):
            label_facts += ("\n" if label_facts else "") + reference_picture_facts()
        else:
            # The case nobody was told about, and it produced the sharpest fabrication in the corpus:
            # on an audio-only brief the writer wrote "anchored by <Audio 1> as the opening frame" and
            # claimed `keyframe completion` with no image attached anywhere. The picture statement was
            # emitted only when a <Picture N> existed, so a brief with no pictures at all was left to
            # infer from the specification in its system prompt, which teaches the construct.
            label_facts += ("\n" if label_facts else "") + NO_PICTURES_AT_ALL
    dlg = "\n".join(f'- {{{{D{i}}}}} ({d.language})'
                      + (f", spoken by {d.speaker_hint}" if d.speaker_hint else "")
                      + (" as an off-screen voiceover" if d.voiceover else "")
                      for i,d in enumerate(brief.dialogue,1)) or "none — the video has no dialogue"

    ask = (
        f"Request: {protected_intent(brief)}\n\n"
        f"Duration is exactly {target.effective_seconds:.3f} seconds; every cut time strictly less "
        f"than {int(target.effective_seconds // 60):02d}:"
        f"{target.effective_seconds % 60:06.3f}, written MM:SS.mmm.\n\n"
        # Three cases, and the middle one used to be told the wrong thing. A base mode with an
        # image attached has a real <Picture 1> and no definition lines at all, so keying the message
        # off the definition lines announced "nothing is attached" over a live reference.
        + (("Facts you must honour — these are the definition lines, use or reword them:\n"
            if is_ref else "Facts you must honour — what each attached picture is:\n")
           + f"{label_facts}\n\n" if label_facts.strip() else
           (f"The only labels that exist for this request are {', '.join(labels)}, and they carry no "
            "separate definitions. Everything else — people, objects, the place — is named in plain "
            "prose, the same way every time it appears.\n\n" if labels else
            "There are NO labels for this request: nothing is attached, so no `<Subject N>`, "
            "`<Picture N>`, `<Video N>` or `<Audio N>` exists. Name people and objects in plain "
            "prose and name them the same way every time.\n\n"))
        +
        f"Dialogue:\n{dlg}\n"
        + (f"\n{DIALOGUE_PLACEMENT}\n" if brief.dialogue else "")
        + (f"\n{reference_audio_words(audio_transcripts)}\n" if audio_transcripts else "")
        # Scoped to the full-reference shape: a base mode has no task-type prefix to constrain, and
        # cannot have an <Audio N> at all -- any audio attachment routes to ref2va (mode.py 12.2#1).
        + (f"\n{audio_task_facts(labels, task_types, audio_roles)}\n" if is_ref else "")
        + (f"\n{video_task_facts(video_roles, task_types)}\n"
           if is_ref and video_task_facts(video_roles, task_types) else "")
        + (f"\n{edit_source_facts(video_worlds, picture_roles)}\n"
           if is_ref and video_worlds else "")
        + (f"\n{swap_facts(picture_roles, video_roles, taken_over)}\n"
           if is_ref and swap_facts(picture_roles, video_roles, taken_over) else "")
    )
    # Ablation lever. Each name drops one block from the ask, so the question "does this block earn
    # its place" is a measurement instead of a belief. Production passes nothing.
    #   facts    the definition lines / the statement of which labels exist
    #   style    what the references look like, or the look the request asked for
    #   licence  which attributes the references govern where the request does not specify them
    #   scope    the creativity setting and its prohibitions
    #   director the profile, as the prose the caller wrote, under a head that says it yields
    def kept(block: str) -> bool:
        return block not in omit

    if "facts" in omit:
        ask = re.sub(r"Facts you must honour[^\n]*\n(?:.*?\n)?\n", "", ask, count=1, flags=re.S)
        ask = re.sub(r"(?:The only labels that exist|There are NO labels)[^\n]*\n\n", "", ask,
                     count=1, flags=re.S)
    if kept("style") and style is not None and style.phrase:
        if style.source == "reference":
            ask += (f"\nThe references look like: {style.phrase}. The request did not ask to change "
                    "the medium, so keep it — do not substitute a different one.\n")
        else:
            ask += f"\nThe request asks for this look: {style.phrase}.\n"
    if kept("licence") and licence is not None:
        free = _reference_governed(licence)
        if free:
            ask += (f"\nWhere the request does not specify them, the references govern "
                    f"{', '.join(free)} — read those off the references rather than inventing them; "
                    "where the request does specify one, the request governs it. Anything neither "
                    "the request nor the references settles is yours.\n")
    if kept("scope") and scope is not None:
        # The dial, stated in the ask rather than baked into the system prompt. The same request at
        # two settings must be able to produce two different answers, so this cannot live in a file.
        ask += "\n" + scope.brief_instruction() + "\n"
    if kept("director") and director is not None:
        ask += "\n" + director_block(director, scope=scope) + "\n"
    # Scoped exactly as P2-too-short is scoped, and off the same two sentences of ref-en.txt 5.2:
    # the range is stated for a full-reference generation task, and editing descriptions are exempt
    # because they "scale with the complexity of the source video".
    if is_ref and generation_task:
        # The caller's pin replaces the freedom clause: two instructions disagreeing about who owns
        # the shot count is exactly how `shots: 3` shipped one shot, silently.
        if brief.shots:
            ask += "\n" + LENGTH_TARGET.replace(
                "however many shots you choose",
                f"exactly the {brief.shots} [Shot] blocks the request pins") + "\n"
            ask += (f"\nThe caller asked for exactly {brief.shots} shot(s). The document must "
                    f"contain exactly {brief.shots} [Shot N] blocks: [Shot 1] with no timestamp, "
                    "every later shot opening with its own 'At MM:SS.mmm' cut time, strictly "
                    "increasing.\n")
        else:
            ask += "\n" + LENGTH_TARGET + "\n"
    if brief.onscreen_text:
        ask += ("\nText that must be visible in frame, in straight double quotes, verbatim: "
                + "; ".join(f'"{t}"' for t in brief.onscreen_text) + "\n")
    if brief.constraints:
        ask += "\nHard constraints:\n" + "\n".join(f"- {c}" for c in brief.constraints)

    reply = backend.chat(
        [{"role": "system", "content": prompt_policy(load_prompt(prompt_name), brief)},
         user_message(ask, images)],
        thinking=(True if thinking is None else thinking),
        # 0.3, not 0.85. The binding constraint on a free write is format compliance, and a
        # side-by-side showed 0.3 returning a near-valid six-section brief while 0.85 drifted into
        # preamble and dropped sections. Creativity here comes from the latitude, not the sampler.
        temperature=0.3, seed=seed, max_tokens=20000)
    from .plan import build_speakers
    speakers = build_speakers(brief, [] if mode and mode.is_base else subjects, [])
    voice = next((label for label, role in audio_roles if role == "voice_timbre"), None)
    for speaker in speakers:
        speaker.voice_ref = voice
    return restore_dialogue(reply.content, brief, speakers)


def _definition_lines(subjects: list[SubjectPlan], cards: dict[str, AssetCard],
                      labels: tuple[str, ...]) -> list[str]:
    """The subject_definitions lines themselves, ready to use or reword."""
    from .plan import taken_definition
    out = []
    for s in subjects:
        # A figure being replaced is handed over with no attribute list at all: this block is what
        # the writer copies into subject_definitions, and an identity list there is conditioning
        # for someone the caller asked to remove.
        if s.taken_over_by:
            out.append(taken_definition(s))
            continue
        attrs = ", ".join(s.attributes) or "as shown in the reference"
        src = ", ".join(s.sources)
        out.append(f"{s.label} is {s.descriptor} in {src}, with {attrs}.")
        if s.pose:
            out.append(f"    (the reference shows {s.label} in a fixed pose; that pose does NOT "
                       "carry over — the request and your beats decide what it does)")
    return out


def plan_shots(backend: Backend, brief: Brief, subjects: list[SubjectPlan],
               cards: dict[str, AssetCard], target, *,
               prompt_name: str = "shotplan.v1.txt", seed: int | None = None,
               thinking: bool | None = None, max_shots: int = 4, licence=None,
               scope=None, director=None):
    """The model decides the edit; shots.validate_proposal proves it legal.

    Thinking defaults ON here and nowhere else. The earlier measurement found reasoning worthless
    because every planning field was code-owned -- there was nothing to think about. This call is
    the first real decision the model has been given, so it is the one place the 3.7x is plausibly
    earned, and the eval loop can now actually test that.
    """
    from .shots import shot_schema, validate_proposal

    # An explicit pin overrides the profile ceiling: intake has already proven it fits the render,
    # and the schema closes both ends so the count cannot come back wrong.
    limit = brief.shots or max_shots
    ask = (
        f"Request: {protected_intent(brief)}\n\n"
        f"Total duration: {target.effective_seconds:.3f} seconds "
        f"({target.frames} frames at 24 fps). Every cut must fall inside it.\n\n"
        f"Available subject labels:\n{_asset_digest(cards, subjects)}\n\n"
    )
    if brief.shots:
        ask += (f"Exactly {brief.shots} shot(s): the caller asked for that count and it is kept "
                "exactly.\n")
    else:
        ask += f"Maximum {limit} shots. One shot is a legitimate answer.\n"
    if brief.constraints:
        ask += "\nHard constraints:\n" + "\n".join(f"- {c}" for c in brief.constraints)
    if licence is not None:
        free = _reference_governed(licence)
        if free:
            ask += (f"\nWhere the request does not specify them, the references govern "
                    f"{', '.join(free)}; where the request does specify one, the request governs it. "
                    "Everything the request and the references BOTH leave open is yours to decide.\n")
    if scope is not None:
        ask += "\n" + scope.brief_instruction() + "\n"
        if scope.forbidden or scope.licensed != frozenset(ELEMENTS):
            # The planner has a suggestions channel, so a withheld idea is not a lost one. This is
            # the honest place for "the clip wants a line here": reported to the caller, not added.
            ask += ("If you think the clip would be better with something this setting does not "
                    "license, say so in `suggestions` — it will be reported, not added.\n")

    if director is not None:
        # The planner picks the edit; the profile does not touch that. It is here because the
        # planner also writes a `beat` per shot, and a beat is prose about what the shot is FOR.
        ask += "\n" + director_block(director, scope=scope) + "\n"

    raw = backend.json_call(
        [{"role": "system", "content": prompt_policy(load_prompt(prompt_name), brief)},
         {"role": "user", "content": ask}],
        shot_schema(limit, exact=brief.shots), required=("shots",), seed=seed, max_tokens=16000,
        thinking=True if thinking is None else thinking)
    return validate_proposal(raw, target, max_shots=limit)


def beat_sheet(backend: Backend, brief: Brief, mode: Mode, subjects: list[SubjectPlan],
               cards: dict[str, AssetCard], shots: list[ShotPlan], *,
               prompt_name: str = "beatsheet.v1.txt", seed: int | None = None,
               thinking: bool | None = None, style=None,
               director=None) -> dict[str, Any]:
    spans = "\n".join(
        f"Shot {s.n}: {s.start_ms / 1000:.2f}s to {s.end_ms / 1000:.2f}s "
        f"({s.duration_ms / 1000:.2f}s long)" for s in shots)
    dlg = "\n".join(f'- {{{{D{i}}}}} ({d.language})' for i,d in enumerate(brief.dialogue,1)) or "none"
    ask = (
        f"Creative request: {protected_intent(brief)}\n\n"
        f"Total duration: {sum(s.duration_ms for s in shots) / 1000:.3f} seconds, "
        f"{len(shots)} shot(s).\n{spans}\n\n"
        f"Available subject labels:\n{_asset_digest(cards, subjects)}\n\n"
        f"Dialogue that will be spoken (inserted later, do not write it):\n{dlg}\n"
    )
    if brief.constraints:
        ask += "\nHard constraints from the caller:\n" + "\n".join(f"- {c}" for c in brief.constraints)
    if director is not None:
        # This call chooses the camera from the closed enum, which is the profile's
        # strongest lever, so a beat sheet written without it is the one that most
        # visibly ignores the setting.
        ask += "\n" + director_block(director) + "\n"
    if style is not None and style.source == "request":
        ask += (f"\nThe caller stated the style: \"{style.phrase}\". Use it verbatim as "
                "style_phrase, optionally adding lighting or colour detail after it. Do not "
                "substitute a different medium.\n")
        if style.discrepancy:
            ask += (f"The references happen to look like {style.observed_phrase!r}. The caller's "
                    "word still wins; do not blend the two into a contradiction.\n")
    elif style is not None and style.observed_phrase:
        ask += (f"\nThe caller stated no style. The references look like "
                f"{style.observed_phrase!r} — use that as the basis for style_phrase.\n")
    if brief.silent:
        ask += ("\nThe caller asked for NO background music: music must be exactly \"N/A\". "
                "Ambient and physical sound still belong in ambient_sound and sync_sound.")

    obj = backend.json_call(
        [{"role": "system", "content": prompt_policy(load_prompt(prompt_name), brief)},
         {"role": "user", "content": ask}],
        beat_schema(len(shots)),
        required=("shots", "style_phrase", "summary", "music"),
        seed=seed, max_tokens=12000, thinking=thinking)
    return _sanitise_beats(obj, subjects, len(shots), style)


def _sanitise_beats(obj: dict[str, Any], subjects: list[SubjectPlan], n: int,
                    style=None) -> dict[str, Any]:
    """The schema constrains shape, not sense. Enforce the enums and label vocabulary here."""
    valid_labels = {s.label for s in subjects}
    shots = obj.get("shots") or []
    clean_shots = []
    for i in range(n):
        s = shots[i] if i < len(shots) else {}
        cam = s.get("camera") or {}
        ctype = cam.get("type") if cam.get("type") in CAMERA_TYPES else None
        clean_shots.append({
            "beat": _one_line(s.get("beat") or ""),
            "camera": {
                "type": ctype,
                "amplitude": cam.get("amplitude") if cam.get("amplitude") in AMPLITUDES else None,
                "speed": cam.get("speed") if cam.get("speed") in SPEEDS else None,
            } if ctype else {},
            "subjects": [x for x in (s.get("subjects") or []) if x in valid_labels],
            "sync_sound": [_one_line(x) for x in (s.get("sync_sound") or []) if x][:3],
        })

    ambient = [_one_line(x) for x in (obj.get("ambient_sound") or []) if x][:3]
    # Enforce the partition: a sound cannot be both synchronized and ambient.
    sync_all = {t.lower() for sh in clean_shots for t in sh["sync_sound"]}
    ambient = [a for a in ambient if a.lower() not in sync_all]

    return {
        "shots": clean_shots,
        "ambient_sound": ambient,
        "style_phrase": _resolve_style_phrase(obj.get("style_phrase") or "", style),
        "summary": _strip_labels(obj.get("summary") or "", keep_subjects=True).strip(),
        "music": _one_line(obj.get("music") or "") or "N/A",
    }


def _resolve_style_phrase(model_phrase: str, style) -> str:
    """A stated style is a requirement, not a suggestion. If the model dropped or replaced the
    caller's medium, put it back in front rather than shipping a substitution."""
    phrase = _strip_labels(_one_line(model_phrase)).rstrip(".")
    if style is None or style.source != "request" or not style.phrase:
        return phrase or (style.phrase if style else "")
    from .style import classify_medium
    if classify_medium(phrase) == style.requested_medium and style.requested_medium:
        return phrase
    head = style.phrase
    rest = ", ".join(x.strip() for x in phrase.split(",") if x.strip()
                     and x.strip().lower() not in head.lower())
    return f"{head}, {rest}" if rest else head


def _one_line(s: str) -> str:
    return re.sub(r"\s+", " ", normalize(str(s))).strip()


def _strip_labels(s: str, keep_subjects: bool = False) -> str:
    """Remove grounded media labels the prose stage must not introduce.

    The harness found that naming attached media in the prompt induces the model to emit
    definition lines for them. Those labels are the template's business, so any that leak
    into prose are removed rather than trusted.
    """
    s = re.sub(r"<\s*(?:Picture|Video|Audio)\s+\d+\s*>", "", s)
    if not keep_subjects:
        s = re.sub(r"<\s*Subject\s+\d+\s*>", "", s)
    return re.sub(r"\s{2,}", " ", s).strip()


def shot_body(backend: Backend, brief: Brief, plan_mode: Mode, shot: ShotPlan,
              subjects: list[SubjectPlan], style_opening: str, *,
              prompt_name: str = "prose_shot.v2.txt", thinking: bool = False,
              seed: int | None = None, images: list[str] | None = None,
              anchor_label: str | None = None, expand_from: str | None = None,
              director=None) -> str:
    """`label_policy` is mode-derived, not a preference.

    `<Subject N>` is a Ref2VA construct: it only means anything because subject_definitions
    defines it, and base modes have no such section. Emitting one in a base mode produces a
    label that names nothing -- the same defect class as <Image 1>. So the instruction and the
    post-clean are both keyed to the mode rather than trusting one wording to cover both.
    """
    ref2va = plan_mode is Mode.REF2VA
    present = [s for s in subjects if s.label in shot.subjects] or subjects
    if ref2va:
        subj_lines = "\n".join(
            f"{s.label} = {s.descriptor}. Appearance (carry these forward): "
            f"{'; '.join(s.attributes) or 'as referenced'}"
            # The plate's pose is NOT named here on purpose. Naming it in order to exclude it
            # made the model negotiate with it instead -- one shot came back with "fists loosely
            # clenched" and the next with "hands relaxed rather than clenched", both of which are
            # the contamination in hedged form. What it is never told, it cannot argue with.
            + (f". Pose comes from the beat, not the reference" if s.pose and not s.pose_licensed
               else "")
            for s in present) or "no referenced subjects"
        label_rule = ("Refer to referenced content by its <Subject N> label exactly as given. "
                      "Never write <Picture N>, <Video N> or <Audio N>.")
    else:
        subj_lines = "\n".join(
            f"{s.descriptor} — appearance: {'; '.join(s.attributes) or 'as referenced'}"
            + (". Pose comes from the beat, not the reference"
               if s.pose and not s.pose_licensed else "")
            for s in present) or "no referenced subjects"
        if anchor_label:
            label_rule = (f"The opening frame is supplied as {anchor_label}. You may write "
                          f"{anchor_label} once when describing what it establishes. Never write "
                          "<Subject N>, <Video N> or <Audio N> — they do not exist in this mode.")
        else:
            label_rule = ("Describe everything in plain words. Never write <Subject N>, "
                          "<Picture N>, <Video N> or <Audio N> — no reference labels exist here.")
    dlg = "\n".join(
        f"{{{{D{i}}}}} = a line spoken by {(d.speaker_hint or 'the speaker')}"
        f"{' as an off-screen voiceover' if d.voiceover else ''} "
        f"({len(d.text.split())} words, {d.language})"
        for i, d in enumerate(shot.dialogue, 1)) or "none"
    sync = "\n".join(f"- {t}" for t in shot.sync_sound) or "none"
    text_on_screen = "\n".join(f'- "{t}"' for t in shot.onscreen_text) or "none"

    ask = (
        f"Creative request: {protected_intent(brief)}\n"
        f"Overall style (already stated elsewhere, do not restate it): {style_opening}\n\n"
        f"You are writing SHOT {shot.n} of {'1' if shot.n == 1 else 'the sequence'}, covering "
        f"{shot.start_ms / 1000:.2f}s to {shot.end_ms / 1000:.2f}s "
        f"({shot.duration_ms / 1000:.2f} seconds of screen time).\n"
        f"What must happen in this shot: {shot.beat or protected_intent(brief)}\n\n"
        f"Subjects visible in this shot:\n{subj_lines}\n\n"
        f"Synchronized sound caused inside this shot (weave in where it occurs):\n{sync}\n\n"
        f"Dialogue placeholders to place:\n{dlg}\n\n"
        f"Text visible in frame:\n{text_on_screen}\n\n"
        f"WORD TARGET: {shot.word_target} words. HARD MAXIMUM: "
        f"{int(shot.word_target * 1.1)} words. Do not exceed the maximum.\n\n"
        f"How to name things in this shot: {label_rule}\n"
    )
    if shot.n > 1:
        ask += ("This shot follows a cut. Open by naming the new framing or subject so the cut "
                "carries new information.\n")
    if shot.camera:
        ask += "Place {{CAM}} exactly once, where the camera move happens.\n"
    if expand_from:
        # Under-writing is the documented failure mode here, so the retry hands back the short
        # draft and asks for more OBSERVATION rather than repeating the request for length.
        ask += ("\nA previous attempt came in short:\n\n" + expand_from
                + "\n\nRewrite it at the target length by describing MORE OF WHAT IS IN THE "
                  "FRAME — surfaces, materials, the ground, the background, the light, the "
                  "direction of each movement. Keep every placeholder. Add no new events.\n")

    if director is not None:
        ask += "\n" + director_block(director) + "\n"

    reply = backend.chat(
        [{"role": "system", "content": prompt_policy(load_prompt(prompt_name), brief)},
         user_message(ask, images)],
        thinking=thinking, temperature=0.8, seed=seed, max_tokens=6000)
    return _clean_body(reply.content, keep_subjects=ref2va, keep_picture=bool(anchor_label))


def _clean_body(s: str, keep_subjects: bool = True, keep_picture: bool = False) -> str:
    s = s.strip()
    s = re.sub(r"^```[a-zA-Z]*\s*|\s*```$", "", s).strip()
    # Strip anything that looks like the model reintroducing structure.
    s = re.sub(r"^\s*(detailed_description|integrated_multimodal_description)\s*:\s*", "", s)
    s = re.sub(r"\[Shot\s+\d+\]\s*", "", s)
    s = re.sub(r"^\s*At\s+\d{2}:\d{2}\.\d{3},?\s*", "", s)
    drop = ["Video", "Audio"]
    if not keep_picture:
        drop.append("Picture")
    if not keep_subjects:
        drop.append("Subject")
    s = re.sub(rf"<\s*(?:{'|'.join(drop)})\s+\d+\s*>", "", s)
    return re.sub(r"[ \t]+", " ", normalize(s)).strip()
