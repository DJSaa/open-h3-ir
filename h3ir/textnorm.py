"""Byte hygiene for model output.

The IR is tokenized verbatim, so a curly apostrophe is not a cosmetic difference -- it is a
different token sequence. Models emit them constantly (the first end-to-end run produced
U+2019 in "dragon's"), so every model output is normalised at the boundary rather than
policed later.

Applied ONLY to generated prose. User dialogue and quoted on-screen text are inserted by the
template after this point and must stay byte-exact, whatever they contain.
"""
from __future__ import annotations

import re

REPLACEMENTS = {
    "‘": "'", "’": "'", "‚": "'", "‛": "'",
    "“": '"', "”": '"', "„": '"', "‟": '"',
    "′": "'", "″": '"',
    " ": " ", " ": " ", " ": " ", " ": " ", " ": " ",
    "​": "", "‌": "", "‍": "", "﻿": "",
    "‐": "-", "‑": "-", "‒": "-", "–": "-",
    "…": "...",
    "〈": "<", "〉": ">", "＜": "<", "＞": ">",
    "（": "(", "）": ")", "［": "[", "］": "]",
    "：": ":", "，": ",", "．": ".",
}
_TABLE = str.maketrans(REPLACEMENTS)

# The em dash is deliberately NOT replaced: the FL2VA and L2VA instruction lines require it.


def normalize(s: str) -> str:
    if not s:
        return ""
    out = s.translate(_TABLE)
    return re.sub(r"[ \t]{2,}", " ", out)


def sentence(s: str) -> str:
    """Capitalise and terminate one clause so a list of sound events reads as prose."""
    s = normalize(s).strip().rstrip(",;")
    if not s:
        return ""
    s = s[0].upper() + s[1:]
    return s if s.endswith((".", "!", "?")) else s + "."


def sentences(items: list[str]) -> str:
    return " ".join(x for x in (sentence(i) for i in items) if x)


def normalize_prose(s: str) -> str:
    """Canonicalize structural prose while preserving user speech and lettering byte-exact."""
    out=[];last=0
    for m in re.finditer(r'<d>.*?</d>|"[^"\n]*"',s,re.S):
        out.extend((normalize(s[last:m.start()]),m.group()));last=m.end()
    return ''.join(out)+normalize(s[last:])
