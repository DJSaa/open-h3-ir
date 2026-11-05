"""The creativity dial: how much the writer may add beyond what was asked.

The owner's bar for the service is four parts -- *directed* (a reason behind every choice),
*proportionate* (a plain ask gets a plain result, an ambitious ask gets an ambitious result),
*complete* (everything H3 expects, populated meaningfully), and *valid*. This module owns
proportionality.

It is an explicit input, not an inference. Reading ambition out of the request -- its length, its
specificity, words like "epic" versus "simple" -- was considered and dropped: it would be wrong
often and, worse, there would be no way to overrule it. A dial is honest. Set restrained, get
restrained.

**The axis is addition, not effort.** The dial does not mean "more shots" or "more camera moves" --
that is the taste-as-mechanics trap the rule audit removed from the validator, and it would put shot
count back in through a side door. What the dial governs is whether the writer may introduce
*content the request never supplied*: words for a character to speak, music for the audience, text
burned into the frame. Those three are decidable, and each carries information that came from
nowhere but the model.

Deliberately NOT on the dial, at any position:
  * shot count and where the cuts fall -- a director's call, never a defect;
  * the camera -- H3 requires a stated camera at every position (P4/P5), so "restrained" means the
    plainest move the request supports, never an unstated one;
  * how good the prose is -- not the validator's business at any setting.

This mirrors `licence.py`, which is the owner's settled rule for reference plates: the plate governs
any attribute the request does not explicitly speak to. Here the request governs, and the dial says
what the writer may do where the request is silent. Same shape, different source of authority.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import Enum

# The three addable elements. Each is content whose source can only be the model, and each is
# decidable from the finished text.
SPEECH = "speech"                 # a <d> block when the caller supplied no lines
SCORE = "music"                   # non_diegetic_music beyond N/A when no music was asked for
                                  # The VALUE is what a person reads; the identifier is code and
                                  # stays, because the patterns below are keyed by it.
ONSCREEN_TEXT = "on-screen text"  # quoted text in frame when the caller supplied none

ELEMENTS = (SPEECH, SCORE, ONSCREEN_TEXT)

# How each element is named to the writer. Plain English, because the instruction goes into a prompt
# and "ONSCREEN_TEXT" is not a thing anyone writes.
_PHRASE = {
    SPEECH: "a spoken line",
    SCORE: "music for the audience",
    ONSCREEN_TEXT: "text visible in the frame",
}


class Creativity(str, Enum):
    """Named positions, not a float.

    A person understands "restrained / balanced / bold / extreme". A 0.0-1.0 slider reads as a
    machine setting, and the service's standing requirement is no comfy-talk in the interface.
    """

    RESTRAINED = "restrained"
    BALANCED = "balanced"
    BOLD = "bold"
    EXTREME = "extreme"


# What each position licenses the writer to introduce where the request is silent.
#
# RESTRAINED renders the request and nothing else. BALANCED adds the audience layer -- music is
# the one addition that serves a request without inventing anything a character does or says, and
# the format gives it a section whose only alternative is N/A. BOLD may put words in a mouth and
# text on the screen, which is where the brief that beat this service sat.
#
# EXTREME licenses NOTHING BEYOND BOLD, and that is deliberate rather than an omission. There are
# exactly three addable elements and bold already has all of them, so a fourth position could only
# be a longer list if the list were extended -- and extending it means inventing events, figures or
# facts about the caller's material, which was proposed, put to the owner, and rejected outright:
# "no no, not hallucination extreme, more like 'go over instead of under on every decision'".
# So extreme is not more addition. It is MAGNITUDE, below.
_LICENCE: dict[Creativity, frozenset[str]] = {
    Creativity.RESTRAINED: frozenset(),
    Creativity.BALANCED: frozenset({SCORE}),
    Creativity.BOLD: frozenset({SCORE, SPEECH, ONSCREEN_TEXT}),
    Creativity.EXTREME: frozenset({SCORE, SPEECH, ONSCREEN_TEXT}),
}

# --------------------------------------------------------------------------- the second axis
#
# MAGNITUDE: not what may be added, but how far every decision is pushed. The dial's two axes move
# together under one control because the interface takes no clutter; they are separable in principle
# and `Scope` keeps them as separate properties so a second control is a later change here and
# nowhere else.
#
# **What magnitude governs: VALUES. What it never governs: COUNTS.** Amplitude, speed, motion type,
# framing, contrast, how completely a beat resolves -- all values, all how strongly one shot is
# played. How many shots and where the cuts fall are counts, they stay off the dial at every
# position, and "at extreme, where it is choosing between N and N+1 shots, take N+1" was declined
# for that reason: shot count is a director's call and the moment a setting licenses more of them it
# is a rule about shot count again, one layer above the validator where nothing catches it.
#
# The axis is the spec's own. base-en.txt 4.3 defines camera motion as motion type + **amplitude** +
# **speed**, with `with small|large amplitude` and `at slow|fast speed` as the values and the note
# that medium and normal are the omitted defaults. So "the far end" is a set of values the format
# already names -- which is what makes this position checkable rather than a matter of opinion.
MAGNITUDE: dict[Creativity, str] = {
    Creativity.RESTRAINED: "plain",
    Creativity.BALANCED: "measured",
    Creativity.BOLD: "assertive",
    Creativity.EXTREME: "maximal",
}

# The values that ARE the far end, in the spec's exact idiom. A brief at `extreme` containing none of
# these has not been played at the setting it was given -- including by omitting amplitude and speed
# altogether, since the spec's own default for silence is medium and normal.
MAXIMAL_CAMERA = ("with large amplitude", "at fast speed")
# `TIMID_CAMERA` -- the small/slow end -- lived here for the rule that forbade it at `extreme`. It went
# with the rule and is deliberately NOT reintroduced: nothing in this system needs to recognise a quiet
# camera move, because no setting objects to one.

# THE DIAL IS DELIBERATELY ASYMMETRIC. This is the design, not a gap, and the next person to measure
# it will want to file it as one -- so it is written down here rather than in a doc nobody opens.
#
#   restrained   ENFORCED. Q2 rejects an addition the setting does not license. A hard floor.
#   balanced     soft. An instruction that leans. Nothing verifies it.
#   bold         soft. An instruction that leans harder. Nothing verifies it.
#   extreme      ENFORCED. Q3 asserts the closed vocabulary appears. A hard ceiling.
#
# So `balanced` and `bold` are indistinguishable in some outputs BY DESIGN. That was measured and
# reported as a finding -- 0 maximal camera values at restrained, balanced and bold alike -- and the
# owner's answer was to narrow the position rather than enforce it: "bold just means if a little nudge
# can do it, don't mechanically enforce it". A nudge that sometimes moves nothing is still a nudge, and
# a model declining it on a given request is an acceptable outcome rather than a failure.
#
# A check for `bold` was built and REMOVED on that instruction. Do not re-add one. If bold's nudge is
# landing too rarely, the lever is the wording in `brief_instruction`, never a rule.
#
#   maximal   at least one maximal value must appear (Q3). Nothing forbids a quiet one.
#
# There is no rule forbidding `with small amplitude` / `at slow speed` at `extreme`, and that is a
# decision rather than an omission. One existed (Q4-extreme-not-committed) and the owner removed it
# when the choice was put to him as everything-maxed-with-no-exceptions versus mostly-maxed-with-
# contrast-allowed. His reason is the thing that would make someone re-add it, so it is written here:
#
#   HOLD, HOLD, THEN HIT is how a lot of real direction works. A setting that forbids the hold cannot
#   express the hit.
#
# `extreme` still has to REACH for big and fast -- Q3 sees to that -- it just does not have to be
# uniformly loud. Do not add a uniformity rule here.
COMMITS_CAMERA = frozenset({"maximal"})

DEFAULT = Creativity.BALANCED


def parse(value: str | Creativity | None) -> Creativity:
    """Accept the position by name; anything unrecognised is the default rather than an error.

    A caller may be an agent that has never read this file, which is the whole point of the API.
    An unknown word must not fail a render -- it falls back to the default and the compiler records
    what it used.
    """
    if isinstance(value, Creativity):
        return value
    if not value:
        return DEFAULT
    try:
        return Creativity(str(value).strip().lower())
    except ValueError:
        return DEFAULT


# Explicit prohibitions, in the shapes a request actually writes them. Conservative on purpose: a
# false positive here silently strips something the caller wanted, so every pattern needs an
# adjacent negator and none of them guesses from tone. "Quiet", "understated" and "minimal" are NOT
# prohibitions -- they are adjectives, and the same reasoning that says a loose adjective cannot
# override a reference plate says it cannot forbid a section either.
_PROHIBITIONS: tuple[tuple[str, str], ...] = (
    (SPEECH, r"\bno\s+(?:dialogue|speech|speaking|talking|spoken\s+words?|voice[- ]?over|lines)\b"),
    (SPEECH, r"\bwithout\s+(?:any\s+)?(?:dialogue|speech|speaking|voice[- ]?over)\b"),
    (SPEECH, r"\b(?:nobody|no\s+one|no-one)\s+(?:speaks|talks|says)\b"),
    (SPEECH, r"\b(?:does\s*n[o']?t|doesn't|don't|do\s*not)\s+(?:speak|talk)\b"),
    (SPEECH, r"\bsilent\s+(?:film|clip|video|scene|throughout)\b"),
    (SCORE, r"\bno\s+(?:background\s+)?(?:music|score|soundtrack)\b"),
    (SCORE, r"\bwithout\s+(?:any\s+)?(?:background\s+)?(?:music|score|soundtrack)\b"),
    (ONSCREEN_TEXT, r"\bno\s+(?:on[- ]?screen\s+)?(?:text|titles?|captions?|subtitles?|lettering)\b"),
    (ONSCREEN_TEXT, r"\bwithout\s+(?:any\s+)?(?:text|titles?|captions?|subtitles?)\b"),
)
_COMPILED = tuple((el, re.compile(p, re.I)) for el, p in _PROHIBITIONS)

# The other direction: the request asked for the element in prose without the caller filling in a
# structured field. "he says hello" is a request for speech even when no verbatim line was supplied,
# and stripping it would be as wrong as adding one unasked. These lose to a prohibition -- "no
# background music" contains the word "music", so precedence matters and is handled in `build`.
_MENTIONS: tuple[tuple[str, str], ...] = (
    (SPEECH, r"\b(?:says?|saying|said|speaks?|speaking|spoken|dialogue|voice[- ]?over|narrat\w+"
             r"|whispers?|shouts?|asks?|answers?|replies|reply|tells?|sings?|singing|lyrics)\b"),
    (SCORE, r"\b(?:music|musical|score|soundtrack|song|theme|orchestral|synthwave)\b"),
    (ONSCREEN_TEXT, r"\b(?:on[- ]?screen\s+text|caption|subtitle|title\s+card|lettering"
                    r"|reading\s+[\"']|sign\s+that\s+reads)\b"),
)
_COMPILED_MENTIONS = tuple((el, re.compile(p, re.I)) for el, p in _MENTIONS)


def _scan(compiled, texts) -> set[str]:
    return {el for el, pat in compiled for t in texts if pat.search(t or "")}


def prohibitions(*texts: str) -> tuple[str, ...]:
    """Elements the caller explicitly ruled out, read from the request and its constraint lines."""
    found = _scan(_COMPILED, texts)
    return tuple(el for el in ELEMENTS if el in found)


def mentions(*texts: str) -> tuple[str, ...]:
    """Elements the request asks for in prose, without a structured field being filled in."""
    found = _scan(_COMPILED_MENTIONS, texts)
    return tuple(el for el in ELEMENTS if el in found)


@dataclass(frozen=True)
class Scope:
    """What this request licenses, decided once and carried through the whole compile."""

    level: Creativity = DEFAULT
    # supplied by the caller, so present in the output is obedience rather than addition
    requested: frozenset[str] = field(default_factory=frozenset)
    # explicitly ruled out; wins over the dial at every position
    forbidden: frozenset[str] = field(default_factory=frozenset)

    @property
    def magnitude(self) -> str:
        """How far every decision is pushed. Separate from `licensed` on purpose — see MAGNITUDE."""
        return MAGNITUDE[self.level]

    @property
    def licensed(self) -> frozenset[str]:
        """Additions permitted here. A prohibition removes the licence at every position."""
        return frozenset(_LICENCE[self.level] - self.forbidden)

    def permits(self, element: str) -> bool:
        return element not in self.forbidden and (
            element in self.requested or element in self.licensed)

    def why_not(self, element: str) -> str:
        """The reason an element is not allowed, in the words the finding should use."""
        if element in self.forbidden:
            return "the request explicitly ruled it out"
        return (f"the request did not ask for it and the {self.level.value} setting does not "
                "license it")

    def note(self) -> str:
        bits = [f"creativity: {self.level.value}"]
        if self.requested:
            bits.append("requested: " + ", ".join(sorted(self.requested)))
        if self.forbidden:
            bits.append("forbidden: " + ", ".join(sorted(self.forbidden)))
        lic = self.licensed - self.requested
        bits.append("may add: " + (", ".join(sorted(lic)) if lic else "nothing beyond the request"))
        return "; ".join(bits)

    def brief_instruction(self) -> str:
        """What the writer is told. States the licence positively and the prohibitions absolutely.

        The addable list is GENERATED from the licence, never hardcoded per position. An earlier
        version wrote bold's latitude as fixed prose and then appended the prohibitions underneath,
        so a request that forbade music produced "you may add music" followed by "music is ruled
        out" — a contradiction we authored, which the model would spend a fix round on.

        Written as latitude rather than as a quota: naming a number of shots or camera moves here
        would be the same mistake as putting shot count in the validator.
        """
        addable = [_PHRASE[e] for e in ELEMENTS if e in self.licensed]
        head = {
            Creativity.RESTRAINED:
                "Creativity: RESTRAINED. Render what the request describes and nothing beyond it. "
                "Every shot, every move and every sound must trace to something the request states "
                "or something the format requires. This is not permission to write less carefully — "
                "the same care, aimed only at what was asked.",
            Creativity.BALANCED:
                "Creativity: BALANCED. Render what the request describes, and shape how it is seen "
                "and heard — the framing, the camera, where a cut serves it, the performance, the "
                "ambience. Do not invent events the request does not contain.",
            Creativity.BOLD:
                "Creativity: BOLD. The request is the seed, not the ceiling. You may escalate — a "
                "performance beat, a turn, a moment the request never mentioned — as long as it "
                "serves what was asked and never contradicts it.\n"
                "  · Camera: if a nudge would carry the shot, take it. `with large amplitude` and "
                "`at fast speed` are the words for it, and the guide omits amplitude and speed to "
                "mean medium and normal — so the middle is what silence buys. Where a request is "
                "genuinely quiet, a quiet camera is the right answer and nothing here overrides that.",
            Creativity.EXTREME:
                "Creativity: EXTREME. Every decision goes to the far end of what the format "
                "supports — not one notch up from the middle, the boldest value available.\n"
                "  · Camera: `with large amplitude` and `at fast speed`, stated explicitly, and "
                "`with small amplitude` / `at slow speed` appear NOWHERE. The guide omits amplitude "
                "and speed to mean medium and normal, so leaving them out plays the shot at the "
                "middle — the opposite of this setting. Take the most assertive motion type the shot "
                "supports, and the most extreme angle.\n"
                "  · Framing: the far end of what the shot allows — an extreme close-up rather than "
                "a close-up, a wide that is genuinely wide.\n"
                "  · Light and contrast: hard falloff over even, deep shadow over lifted.\n"
                "  · Performance: a beat that fully completes. A hold that actually holds.\n"
                "This is magnitude, not invention. You are playing the requested scene as hard as "
                "the format allows — not adding events, figures, or facts about the material that "
                "the request and the references do not contain.",
        }[self.level]
        out = [head]
        if addable:
            out.append("Beyond the request you may add: " + ", ".join(addable) + ".")
        else:
            out.append("Add none of these unless the request supplies it: "
                       + ", ".join(_PHRASE[e] for e in ELEMENTS) + ".")
        if self.forbidden:
            out.append("The request explicitly rules these out, and no setting overrides that: "
                       + ", ".join(sorted(self.forbidden)) + ".")
        return "\n".join(out)


def build(intent: str, *, level: str | Creativity | None = None,
          constraints: tuple[str, ...] | list[str] = (),
          has_dialogue: bool = False, has_onscreen_text: bool = False,
          has_music_reference: bool = False,
          dialogue_texts: tuple[str, ...] = (),
          silent: bool = False) -> Scope:
    """Read the scope off the request once.

    Precedence, and the order matters:
      1. A prohibition beats a mention of the same word — "no background music" says *music*, and
         reading that as a request for music inverts the instruction.
      2. Content the caller actually supplied beats a prohibition — filling in a dialogue line and
         also writing "no dialogue" is a contradiction, and the concrete lines are the better
         evidence of intent than a phrase that may be stale in a long request.

    `silent` is the caller's structured no-music flag and is treated exactly like the phrase in the
    request, so the flag and the prose cannot disagree.
    """
    texts = (intent, *constraints)
    forbidden = set(prohibitions(*texts))
    if silent:
        forbidden.add(SCORE)
    supplied = {el for el, present in ((SPEECH, has_dialogue),
                                       (ONSCREEN_TEXT, has_onscreen_text)) if present}
    # A quoted span in the request is the caller supplying the words themselves — the strongest
    # form of a request. Spans that are the caller's dialogue lines are speech (carried by
    # `has_dialogue`); whatever quoted material remains is lettering the caller wants in frame.
    # Measured (2026-08-15): four tray briefs died on their own Q2 because the sentence's quotes —
    # the node's documented mechanism for both channels — licensed neither.
    spoken = {" ".join(d.split()) for d in dialogue_texts}
    lettering = [q for t in texts for q in re.findall(r'"([^"]+)"', t)
                 if " ".join(q.split()) not in spoken]
    if lettering:
        supplied.add(ONSCREEN_TEXT)
    mentioned = (set(mentions(*texts)) | ({SCORE} if has_music_reference else set())) - forbidden
    forbidden -= supplied
    return Scope(level=parse(level), requested=frozenset(supplied | mentioned),
                 forbidden=frozenset(forbidden))
