"""Resolve explicit motion-only prose when an uploaded video's role was omitted.

Keep stated roles and ambiguous requests unchanged. This is deliberately not a general
natural-language classifier; it recognises an affirmative, scoped motion-only clause.
"""
from dataclasses import replace
import re
from .models import AssetKind, Role


def resolve_motion_references(brief):
    videos = [a for a in brief.assets if a.kind is AssetKind.VIDEO]
    changed = {}
    for n, asset in enumerate(videos, 1):
        if asset.role_stated or asset.role is not Role.EDIT_SOURCE:
            continue
        labels = rf"(?:<Video\s+{n}>|视频\s*{n}(?!\d))"
        if len(videos) == 1:
            labels = rf"(?:{labels}|视频(?!\s*\d)|the video)"
        # No punctuation or another reference label may intervene. Do not match 'not only'.
        zh = labels + r"\s*(?:只|仅)(?:参考|借用|借|提供|给)[^。！？；;\n<>]{0,20}?(?:转头|动作方向|动作节奏|动作)"
        en = rf"(?:use|reference|borrow)\s+{labels}\s+(?:only|solely)\s+for\s+(?:the\s+)?(?:head[- ]turn|subject motion|motion|action)"
        match = re.search(zh, brief.intent, re.I) or re.search(en, brief.intent, re.I)
        if not match:
            continue
        if re.search(r"(?:不要|不能|并非|并不|不是|不得|not|don't|never)\s*$", brief.intent[max(0, match.start()-12):match.start()], re.I):
            continue
        changed[id(asset)] = replace(asset, role=Role.MOTION_REFERENCE)
    return replace(brief, assets=[changed.get(id(a), a) for a in brief.assets]) if changed else brief
