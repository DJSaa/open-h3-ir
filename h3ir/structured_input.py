"""Conservative recognition of existing H3 documents, never a user-input classifier.

An uncertain parse goes to the existing writer with the original request intact.
Only recognised structure becomes a constraint. No global marker stripping.
"""
from dataclasses import dataclass, field
import hashlib
import re

VERSION = "structured-input-v1"
NAMES = ("subject_definitions", "summary", "retention_analysis", "detailed_description",
         "integrated_multimodal_description", "overall_soundscape", "non_diegetic_music")
HEADER = re.compile(r"(?m)^(" + "|".join(NAMES) + r")\s*:")
SHOT = re.compile(r"\[Shot\s+(\d+)\]\s*(?:At\s+(\d+):(\d{2})\.(\d{3}),?\s*)?")
SPEECH = re.compile(r"<d>\s*\[([^]\n]+)\]\s*(.*?)</d>", re.S)

@dataclass
class ParsedInput:
    original: str
    reliable: bool = False
    reasons: list[str] = field(default_factory=list)
    sections: dict[str, str] = field(default_factory=dict)
    shots: list[dict] = field(default_factory=list)

    def evidence(self):
        return {"version": VERSION, "route": "reliable" if self.reliable else "needs_understanding",
                "reasons": self.reasons, "original_sha256": hashlib.sha256(self.original.encode()).hexdigest(),
                "shot_numbers": [s["number"] for s in self.shots] if self.reliable else [],
                "cut_ms": [s["start_ms"] for s in self.shots] if self.reliable else []}

def _masked(text):
    # Protect quoted literal labels, including dialogue and visible lettering.
    return re.sub(r'<d>.*?</d>|"[^"\n]*"|“[^”]*”|「[^」]*」',
                  lambda m: " " * len(m.group()), text, flags=re.S)

def parse(text):
    p = ParsedInput(text)
    scan = _masked(text)
    headers = list(HEADER.finditer(scan))
    if not headers:
        p.reasons.append("no_complete_h3_sections")
        return p
    keys = [m[1] for m in headers]
    if len(set(keys)) != len(keys):
        p.reasons.append("duplicate_sections")
        return p
    prefix = text[:headers[0].start()].strip()
    if prefix and not (prefix.startswith("How the reference pictures align with the target video")
                       and "\n" not in prefix):
        p.reasons.append("unassigned_prefix")
    for i,m in enumerate(headers):
        end = headers[i+1].start() if i+1 < len(headers) else len(text)
        p.sections[m[1]] = text[m.end():end].strip()
    main = "detailed_description" if "detailed_description" in keys else "integrated_multimodal_description"
    expected = list(NAMES[:4]) + list(NAMES[-2:]) if main == "detailed_description" else [main,*NAMES[-2:]]
    if keys != expected or any(not p.sections.get(k) for k in expected):
        p.reasons.append("incomplete_or_reordered_sections")
    if text.count("<d>") != text.count("</d>"):
        p.reasons.append("unclosed_dialogue")
    # Unknown field-looking lines or code fences require semantic understanding.
    if "```" in text or re.search(r"(?m)^\w[\w -]*[:：]", HEADER.sub("", scan)):
        p.reasons.append("unassigned_field_or_wrapper")
    body = p.sections.get(main, "")
    matches = list(SHOT.finditer(_masked(body)))
    if not matches or [int(m[1]) for m in matches] != list(range(1,len(matches)+1)):
        p.reasons.append("ambiguous_shot_sequence")
    for i,m in enumerate(matches):
        ms = (int(m[2])*60_000+int(m[3])*1000+int(m[4])) if m[2] else None
        if i == 0 and ms is not None or i > 0 and ms is None:
            p.reasons.append("ambiguous_shot_time")
        end = matches[i+1].start() if i+1 < len(matches) else len(body)
        p.shots.append({"number":int(m[1]),"start_ms":0 if i==0 else ms,"body":body[m.end():end].strip()})
    if any(not s["body"] for s in p.shots):
        p.reasons.append("empty_shot")
    p.reliable = not p.reasons
    return p

def constraints(parsed, candidate):
    """Check explicit input cuts; a label citation elsewhere is never a shot."""
    from .models import Finding
    if not parsed.reliable:
        return []
    after = parse(candidate)
    # General validator reports malformed output. Here only compare parseable cuts.
    if not after.reliable:
        return []
    before = [(s["number"],s["start_ms"]) for s in parsed.shots]
    actual = [(s["number"],s["start_ms"]) for s in after.shots]
    if before != actual:
        return [Finding("DINGO-input-shot-structure", "ERROR",
                        f"Preserve the input's explicit shots and cut times (milliseconds): {before}; got {actual}")]
    return []
