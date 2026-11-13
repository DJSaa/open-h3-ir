"""Narrow compatibility classification after all compiler checks, without text edits.

D11 itself stays strict. Only a late incoming marker for an unambiguous two-part
utterance may be advisory after every other hard check passed. A missing marker,
unknown speaker/voice, changed words/order, or another ERROR keeps D11 blocking.
"""
from dataclasses import replace
import re

from .language import canonical_language
from .structured_input import HEADER, SPEECH, _masked

VERSION = 'late-scenetrans-warning-v1'
D11 = 'D11-split-line-no-scenetrans'
SHOT = re.compile(r'\[Shot\s+(\d+)\]')
SID = re.compile(r'\(S\d+\)')
MARK = '<scenetrans>'


def classify_late_scenetrans(text, brief, speakers, findings):
    """Return findings with eligible D11s as WARN; never change the prompt.

    The input findings must include the compiler's structural, literal-inventory,
    language, schedule, and voice-binding checks. This is not an alternate validator.
    Ambiguity keeps the original ERROR; this does not infer a new speech schedule.
    """
    errors = [f for f in findings if f.severity == 'ERROR']
    if not errors or any(f.rule != D11 for f in errors):
        return findings
    if not brief.dialogue or any(f.rule == 'DINGO-voice-binding-unresolved' for f in findings):
        return findings
    headers = list(HEADER.finditer(_masked(text)))
    mains = [(i,h) for i,h in enumerate(headers)
             if h[1] in ('detailed_description', 'integrated_multimodal_description')]
    if len(mains) != 1:
        return findings
    i, head = mains[0]
    body = text[head.end():headers[i+1].start() if i+1 < len(headers) else len(text)]
    blocks = list(SPEECH.finditer(body))
    # Speech outside the shots cannot count as fulfilling an utterance, even if
    # its characters accidentally balance an omitted line in the inventory check.
    if len(blocks) != len(list(SPEECH.finditer(text))):
        return findings
    scan = _masked(body)
    parts = [' '.join(b[2].split()) for b in blocks]
    late = 0
    cursor = 0
    for line in brief.dialogue:
        want = ' '.join(line.text.split())
        if not want:
            return findings
        end = next((j for j in range(cursor, len(parts))
                    if want in (''.join(parts[cursor:j+1]), ' '.join(parts[cursor:j+1]))), None)
        if end is None:
            return findings
        assigned = [s for s in speakers if s.descriptor == (line.speaker_hint or '')]
        if len(assigned) != 1:
            return findings
        speaker = assigned[0]
        voice_seen = False
        for k in range(cursor, end+1):
            if not parts[k] or canonical_language(blocks[k][1]) != canonical_language(line.language, line.text):
                return findings
            start = blocks[k-1].end() if k else 0
            shots = list(SHOT.finditer(scan, 0, blocks[k].start()))
            if not shots:
                return findings
            start = max(start, shots[-1].end())
            prefix = scan[start:blocks[k].start()]
            sids = list(SID.finditer(prefix))
            if not sids or sids[-1].group() != speaker.sid:
                return findings
            subjects = re.findall(r'<Subject\s+\d+>', prefix[:sids[-1].start()])
            if speaker.subject and subjects and subjects[-1] != speaker.subject:
                return findings
            # Repeated introductions may repeat the SAME SID after its voice
            # clause. Read that contiguous speaker-owned prefix, not just the
            # final SID, without borrowing another speaker's voice evidence.
            owner = len(sids)-1
            while owner and sids[owner-1].group() == speaker.sid:
                owner -= 1
            voices = set(re.findall(r'<Audio\s+\d+>', prefix[sids[owner].end():]))
            if voices and voices != {speaker.voice_ref}:
                return findings
            voice_seen |= speaker.voice_ref in voices
        if speaker.voice_ref and not voice_seen:
            return findings
        for k in range(cursor, end):
            gap = scan[blocks[k].end():blocks[k+1].start()]
            cuts = list(SHOT.finditer(gap))
            if len(cuts) != 1:
                return findings
            if gap.count(MARK) >= 2:
                continue
            # Only a completed two-part utterance is unambiguous. In a longer
            # split a trailing marker might belong to the NEXT outgoing join.
            if end-cursor != 1 or gap.count(MARK) != 1:
                return findings
            before_cut = gap[:cuts[0].start()]
            if before_cut.count(MARK) != 1:
                return findings
            left_shots = list(SHOT.finditer(scan, 0, blocks[k].start()))
            if int(cuts[0][1]) != int(left_shots[-1][1])+1:
                return findings
            tail_end = blocks[k+2].start() if k+2 < len(blocks) else len(body)
            tail = scan[blocks[k+1].end():tail_end]
            # The second marker must immediately follow the final spoken part,
            # with no extra cut or marker that could claim it for another join.
            if not re.match(r'\s*<scenetrans>', tail) or tail.count(MARK) != 1 or SHOT.search(tail):
                return findings
            late += 1
        cursor = end+1
    if cursor != len(blocks) or late != len(errors):
        return findings
    return [replace(f, severity='WARN', msg=f.msg +
                    ' Compatibility warning: both markers are present, but the incoming marker '
                    'immediately follows the final part. Exact utterance order, count, language '
                    'and explicit speaker/voice checks passed, with no other hard errors; '
                    'the prompt was retained unchanged (' + VERSION + ').')
            if f.rule == D11 and f.severity == 'ERROR' else f for f in findings]
