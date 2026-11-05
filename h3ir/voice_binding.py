"""Resolve explicit voice references once, shared by drafting and dialogue restoration.

Unspecified multi-voice assignments stay unresolved rather than taking Audio 1.
Only voice_timbre assets participate; music and synchronized tracks never do.
"""
import re
from .models import Finding

AUDIO = re.compile(r"<Audio\s+(\d+)>|音频\s*(\d+)|\bAudio\s+(\d+)\b", re.I)
BIND = re.compile(r"音色|声音|参考|借|用|voice|timbre|refer|use", re.I)

def aliases(hint):
    picture = re.fullmatch(r"<Picture\s+(\d+)>|图\s*(\d+)", hint, re.I)
    if picture:
        n = next(x for x in picture.groups() if x)
        return rf"(?:<Picture\s+{n}>|图\s*{n}(?!\d))"
    return re.escape(hint)

def resolve(brief, roles):
    from .language import QUOTES
    # Dialogue may itself name an audio file; it is not a binding instruction.
    text = QUOTES.sub("", brief.intent)
    hints = list(dict.fromkeys(d.speaker_hint or "" for d in brief.dialogue))
    allowed = {label for label, role in roles if role == "voice_timbre"}
    candidates = {h: set() for h in hints}
    patterns = {h: aliases(h) for h in hints if h}
    for clause in re.split(r"[。！？;；\n.!?]", text):
        refs = [(m.start(), '<Audio '+next(x for x in m.groups() if x)+'>') for m in AUDIO.finditer(clause)]
        if not refs or not BIND.search(clause):
            continue
        mentions = sorted((m.start(), h, m.end()) for h, pat in patterns.items() for m in re.finditer(pat, clause, re.I))
        if re.search(r"分别|respectively", clause, re.I):
            order = list(dict.fromkeys(h for _, h, _ in mentions))
            if not order and re.search(r"两人|两个人|两位|both|their voices", clause, re.I):
                # "Their voices respectively ..." follows the named participants in the request.
                named = sorted((m.start(), h) for h, pat in patterns.items() for m in re.finditer(pat, text, re.I))
                order = list(dict.fromkeys(h for _, h in named))
            if len(order) == len(refs):
                for h, (_, label) in zip(order, refs): candidates[h].add(label)
            continue
        for i, (start, h, end) in enumerate(mentions):
            stop = mentions[i+1][0] if i+1 < len(mentions) else len(clause)
            local = [(pos,label) for pos,label in refs if end <= pos < stop]
            if len(local) == 1 and BIND.search(clause[end:stop]):
                # Negated instructions must not become positive assignments.
                if not re.search(r"不要|不能|不使用|不参考|do not|don't|not use", clause[end:stop], re.I):
                    candidates[h].add(local[0][1])
    result = {}
    for h, values in candidates.items():
        result[h] = next(iter(values)) if len(values) == 1 and values <= allowed else None
    if len(hints) == 1 and len(allowed) == 1 and not candidates[hints[0]] and not AUDIO.search(text):
        result[hints[0]] = next(iter(allowed))
    return result

def findings(text, speakers, roles=()):
    """Check explicit speaker/audio pairs in final prose, outside literal dialogue."""
    clean = re.sub(r'<d>.*?</d>', '', text, flags=re.S)
    found = []
    for sp in speakers:
        if not sp.voice_ref:
            if any(role == "voice_timbre" for _, role in roles):
                found.append(Finding("DINGO-voice-binding-unresolved", "WARN",
                    f"No unambiguous voice reference for {sp.sid}; no default audio was assigned."))
            continue
        sid = re.escape(sp.sid)
        # Both forms emitted by the compiler: speaker ... Audio, or Audio ... speaker.
        for line in clean.splitlines():
            for m in re.finditer(sid + r'([^.!?\n]*?)(?=\(S\d+\)|[.!?\n]|$)', line):
                segment=m.group(1)
                if not re.search(r"voice|timbre|音色|声音", segment, re.I):
                    continue
                # A definition/prose sentence may end before its binding clause.
                for audio in re.findall(r'<Audio\s+\d+>', segment):
                    if audio != sp.voice_ref:
                        found.append(Finding('DINGO-voice-binding', 'ERROR', f'{sp.sid} must use {sp.voice_ref}, not {audio}'))
            for m in re.finditer(r'(<Audio\s+\d+>) is (?:a |the )?voice[- ]timbre reference[^.\n]*?' + sid, line, re.I):
                if m.group(1) != sp.voice_ref:
                    found.append(Finding('DINGO-voice-binding', 'ERROR', f'{sp.sid} must use {sp.voice_ref}, not {m.group(1)}'))
    return found
