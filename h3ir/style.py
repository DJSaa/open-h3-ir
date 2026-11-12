"""Style resolution. The policy is set by licence.py; this module only renders it.

**The reference governs the medium unless the request explicitly asks for a transformation.** A
bare adjective ("anime style, cinematic") is not a transformation -- see licence.py for the
owner's reasoning, which is drift: a throwaway word must not be able to override an observed
plate, or a character drifts between shots according to how carelessly each request was worded.

I previously had this the other way round and was wrong. The argument I made -- that silently
ignoring a stated instruction is undiagnosable -- only holds if the word was deliberate.
"Reimagine as" is deliberate; "anime style" dropped into a shot description is not.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from .models import AssetCard, AssetKind, Brief, Role

# A coarse medium taxonomy. Coarse on purpose: this runs in the draft path, where there is no
# model, and its only job is to notice that two descriptions point at different media.
MEDIUM_BUCKETS: dict[str, tuple[str, ...]] = {
    "2d-animation": ("anime", "manga", "2d animation", "hand-drawn", "cel-shaded", "cel shaded",
                     "cartoon", "toon", "comic book", "comic illustration", "comic", "graphic novel",
                     "illustrated animation", "illustration", "illustrated", "line art",
                     "inked", "vector art"),
    "3d-animation": ("3d cg", "3d animation", "3d computer animation", "cgi", "pixar",
                     "video game cutscene", "game cinematic", "rendered in 3d", "unreal engine"),
    "live-action": ("live-action", "live action", "photographic", "photoreal", "photorealistic",
                    "documentary", "found footage", "cinematic live"),
    "stop-motion": ("stop-motion", "stop motion", "claymation", "clay animation", "puppet",
                    "papercraft", "paper cut-out"),
    "painted": ("watercolour", "watercolor", "oil painting", "gouache", "ink wash", "storybook",
                "painterly", "pastel drawing", "charcoal"),
    "archival": ("vintage film", "super 8", "16mm", "vhs", "black and white film", "newsreel"),
}

# Words that describe treatment rather than medium. They never create a discrepancy on their own,
# because "cinematic" is compatible with every medium above.
TREATMENT_TERMS = ("cinematic", "moody", "high-contrast", "chiaroscuro", "grainy", "soft",
                   "dramatic", "muted", "saturated", "backlit", "neon", "warm", "cold")

ALL_STYLE_TERMS: tuple[str, ...] = tuple(
    sorted({t for terms in MEDIUM_BUCKETS.values() for t in terms} | set(TREATMENT_TERMS),
           key=len, reverse=True))


def classify_medium(text: str) -> str | None:
    """Which medium bucket a description points at, or None if it names no medium."""
    low = (text or "").lower()
    hits: list[tuple[int, str]] = []
    for bucket, terms in MEDIUM_BUCKETS.items():
        for t in terms:
            m = re.search(r"(?<!\w)" + re.escape(t) + r"(?!\w)", low)
            i = m.start() if m else -1
            if i >= 0:
                hits.append((i, bucket))
    if not hits:
        return None
    hits.sort()
    return hits[0][1]


def style_terms_in(text: str) -> list[str]:
    """Style words the caller actually wrote, in the order they wrote them."""
    low = (text or "").lower()
    found: list[tuple[int, str]] = []
    for t in ALL_STYLE_TERMS:
        m = re.search(r"(?<!\w)" + re.escape(t) + r"(?!\w)", low)
        i = m.start() if m else -1
        if i < 0:
            continue
        # Skip a term already covered by a longer overlapping match.
        if any(j <= i and i + len(t) <= j + len(u) for j, u in found):
            continue
        found.append((i, t))
    found.sort()
    return [t for _, t in found]


@dataclass
class StyleDecision:
    phrase: str                   # the comma-form phrase both modes render from
    source: str                   # "request" | "reference" | "default"
    requested_medium: str | None = None
    observed_medium: str | None = None
    observed_phrase: str = ""

    @property
    def discrepancy(self) -> bool:
        return bool(self.requested_medium and self.observed_medium
                    and self.requested_medium != self.observed_medium)

    def note(self) -> str | None:
        if not self.discrepancy:
            return None
        if self.source == "reference":
            return (f"the request mentions {self.requested_medium!r} but the reference reads as "
                    f"{self.observed_medium!r}, and the brief keeps the reference. A bare style "
                    "word is not an instruction to depart from an attached plate — that would let "
                    "a loose adjective drift the character between shots. To change the look, ask "
                    "for it: 'reimagine as anime', 'in the style of ...', 'restyle to ...'.")
        return (f"the request asks to transform the look to {self.requested_medium!r} while the "
                f"reference reads as {self.observed_medium!r}; the plate's identity is carried "
                "and its style is replaced (attribute_transfer)")


def _tidy(s: str) -> str:
    """Card prose is a sentence fragment; a style phrase is a noun phrase."""
    t = (s or "").strip().rstrip(". ")
    t = re.sub(r"^(?:resembling|similar to|like|reminiscent of|suggesting)\s+", "", t, flags=re.I)
    t = re.sub(r"^(?:a|an|the)\s+", "", t, flags=re.I)
    t = re.sub(r"\s+styles?$", "", t, flags=re.I)
    return t


def observed_style(cards: dict[str, AssetCard], prefer: str = "") -> str:
    """The reference's rendering MEDIUM, and its lighting only when the lighting belongs to a scene.

    A character sheet is lit for legibility -- even, diffuse, studio -- and that lighting is a
    property of the sheet, not of the video. Carrying it over put "even, diffuse studio lighting
    with soft shadows cast on the floor" into a torchlit corridor.

    `prefer` is the sha256 of a clip attached as `edit_source`, and it wins over every plate. On an
    edit the target video IS that clip with a change made to it (ref-en.txt 3 mandates the sentence
    saying so), so the clip owns the look by definition -- and the loop below walks a dict and takes
    the first IMAGE it finds, which on a four-seed measurement was a studio portrait of the person
    being swapped IN. Its style phrase, "clean, high-resolution studio photography with a neutral
    documentary aesthetic", became the target video's style opening every single time, and the
    workshop the clip was shot in appeared in none of them.
    """
    preferred = cards.get(prefer) if prefer else None
    if preferred is not None and preferred.style:
        style = _tidy(preferred.style)
        light = _tidy(preferred.lighting or "")
        return f"{style}, {_lower_first(light)}" if light else style
    for c in cards.values():
        if c.kind is AssetKind.IMAGE and c.style:
            style = _tidy(c.style)
            if c.is_reference_sheet:
                return style
            light = _tidy(c.lighting or "")
            return f"{style}, {_lower_first(light)}" if light else style
    return ""


def _lower_first(s: str) -> str:
    return s[0].lower() + s[1:] if s and s[0].isupper() and not s[:3].isupper() else s


def resolve_style(brief: Brief, cards: dict[str, AssetCard],
                  licence=None) -> StyleDecision:
    """Render the licence decision as the comma-form phrase both modes use.

    Treatment words ("cinematic", "moody") are kept whoever governs the medium, because they are
    compatible with every medium and describe the shot rather than the render technique.
    """
    from .licence import MEDIUM, resolve_licence, transform_target
    lic = licence or resolve_licence(brief, cards)

    context = brief.requirements_context or {}
    explicit = context.get("style", {}).get("description")
    if explicit:
        return StyleDecision(phrase=explicit, source="request", requested_medium=classify_medium(explicit))
    asked_terms = style_terms_in(brief.intent)
    asked_medium = [t for t in asked_terms if t not in TREATMENT_TERMS]
    asked_treatment = [t for t in asked_terms if t in TREATMENT_TERMS]
    edit_source = next((a.sha256 for a in brief.assets
                        if a.kind is AssetKind.VIDEO and a.role is Role.EDIT_SOURCE), "")
    observed = observed_style(cards, prefer=edit_source)
    req_medium = classify_medium(brief.intent)
    obs_medium = classify_medium(observed)

    # The request governs the medium ONLY when it asked for a transformation. The target then
    # comes from the request's own words, because a transformation can name a style no closed
    # vocabulary contains ("a 1990s cel animation").
    if lic.governs.get(MEDIUM) == "request":
        target = ", ".join(asked_medium[:2]) or transform_target(brief)
        phrase = ", ".join([t for t in [target] + asked_treatment[:2] if t])
        if phrase:
            return StyleDecision(phrase=phrase, source="request",
                                 requested_medium=req_medium or classify_medium(target),
                                 observed_medium=obs_medium, observed_phrase=observed)
    if observed:
        # The plate supplies the medium; the caller's treatment words ride on top, and a medium
        # word they used loosely is dropped rather than blended into a contradiction.
        extra = [t for t in asked_treatment if t.lower() not in observed.lower()]
        phrase = ", ".join([observed] + extra[:2]) if extra else observed
        return StyleDecision(phrase=phrase, source="reference", requested_medium=req_medium,
                             observed_medium=obs_medium, observed_phrase=observed)
    if asked_medium or asked_treatment:
        return StyleDecision(phrase=", ".join((asked_medium + asked_treatment)[:3]),
                             source="request", requested_medium=req_medium)
    return StyleDecision(phrase="Live-action, cinematic", source="default")
