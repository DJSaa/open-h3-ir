"""Speech-duration evidence, not an enforced speaking-rate contract.

Only explicit out-of-range cut times are errors. Rate estimates stay advisory:
slow speech, singing and caller-pinned timing must never be silently rewritten.
"""
import json
import re
from .models import Finding

VERSION = "dialogue-schedule-v2"
HAN = re.compile(r"[\u3400-\u9fff]")

def evidence(brief):
    lines = []
    for i, line in enumerate(brief.dialogue, 1):
        han = len(HAN.findall(line.text))
        words = len(re.findall(r"[A-Za-z]+(?:'[A-Za-z]+)?", line.text))
        units = han + words * 1.5
        parts = [p for p in re.split(r"(?<=[，,。.!?！？；;])", line.text) if p.strip()]
        lines.append({"id": i, "speaker_hint": line.speaker_hint,
            "language": line.language, "characters": len(line.text),
            "ordinary_speech_seconds": [round(units / 6, 2), round(units / 3, 2)],
            "ordered_part_characters": [len(p) for p in parts]})
    return {"version": VERSION, "video_seconds": brief.seconds,
            "utterances": lines, "estimate_is_advisory": True}

def guidance(brief):
    if not brief or not brief.dialogue:
        return ""
    return ("\nSPEECH SCHEDULING EVIDENCE\n" + json.dumps(evidence(brief), ensure_ascii=False) +
        "\nThe video duration is NOT the dialogue duration. The ranges describe ordinary speech, "
        "not mandatory rates. A voice-timbre reference duration describes the SOURCE audio only; "
        "it does not set the duration of the target utterance and does not license copying its "
        "words or filling its length with speech. This distinction applies to voice_timbre, "
        "not to explicitly requested audio reuse or music synchronization. "
        "Preserve explicit caller timing, slow delivery, pauses, singing and "
        "actions; infer these from ORIGINAL REQUIREMENTS, not from the previous draft. "
        "For unspecified timing, map each protected line to its beginning, ending, and speaker. "
        "Do not stretch short dialogue to occupy the whole video. A mid-sentence cut must happen "
        "during the supplied sentence, with its next protected part continuing immediately, not "
        "after several seconds of unassigned talking. Place that cut using the length of the "
        "preceding part, not a fraction of the total video duration. After the last supplied line, "
        "explicitly end the speech event; use closed-mouth listening, gestures or other compatible "
        "nonverbal actions and room tone for the remaining time. Do not remove requested cuts. "
        "For each line audit the FINAL document: where does it start and finish, is there an "
        "unassigned speaking interval, and what do the characters do after it? Repair invented "
        "timing and actions when they conflict with speech continuity. Keep caller-pinned timing; "
        "never delete or abbreviate a line to make it fit. Do not output this evidence or an "
        "extra schedule section; express the arrangement in the existing H3 shot descriptions.\n")

def findings(text, brief):
    if not brief.dialogue:
        return []
    # Inspect the shipped shot headers, never the deterministic draft plan.
    out = []
    for m in re.finditer(r"\[Shot\s+\d+\]\s*(?:<scenetrans>\s*)?At\s+(\d+):(\d+(?:\.\d+)?)", text, re.I):
        seconds = int(m[1]) * 60 + float(m[2])
        if seconds >= brief.seconds:
            out.append(Finding("DINGO-dialogue-cut-outside-video", "ERROR",
                "A dialogue shot begins outside the requested video duration. Preserve supplied "
                "speech; repair draft-invented timing. Do not discard caller constraints."))
    return out
