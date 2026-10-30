"""Input-language policy and byte-preserving speech extraction.

Language is inferred from instructions outside quoted user content. Speech is a separate
channel: its language comes from the explicit delivery request, otherwise from the line.
Nothing in this module translates user text or guesses spoken content from audio.
"""
from __future__ import annotations

import re
from dataclasses import replace
from .models import Brief, DialogueLine, Finding

POLICY_VERSION = "dingo-language-v1"
QUOTES = re.compile(r'“([^”]*)”|「([^」]*)」|『([^』]*)』|"((?:\\.|[^"\\])*)"|(?<!\w)\'([^\'\n]+)\'', re.S)
HAN = re.compile(r'[\u3400-\u9fff]')
SPEECH = re.compile(r'说(?:道|一句|一次|出)?|回答|回复|喊(?:道)?|念(?:出)?|口播|台词|对白|朗读|旁白|\b(?:say|says|said|saying|speak|speaks|speaking|spoken|reply|replies|replied|replying|shout|shouts|shouting|whisper|whispers|whispering|dialogue|voiceover|reads?|reading)\b', re.I)
LETTERING = re.compile(r'字幕|标牌|招牌|屏幕(?:上)?(?:显示|文字)|写着|文字|\b(?:subtitle|caption|sign|lettering|onscreen|on-screen|written|reads)\b', re.I)
ALIASES = {'zh':'Chinese','zh-cn':'Chinese','chinese':'Chinese','mandarin':'Chinese','中文':'Chinese','普通话':'Chinese','汉语':'Chinese','en':'English','en-us':'English','english':'English','英语':'English','英文':'English'}


def quoted(text: str):
    for m in QUOTES.finditer(text):
        yield m, next(x for x in m.groups() if x is not None)


def content_language(text: str) -> str:
    return 'Chinese' if HAN.search(text) else 'English'


def description_language(intent: str) -> str:
    text = QUOTES.sub('', intent)
    text = re.sub(r'<[^>]+>|\b(?:Picture|Subject|Video|Audio)\s+\d+', '', text)
    han = len(HAN.findall(text))
    words = len(re.findall(r'[A-Za-z]+', text))
    return 'Chinese' if han and han >= words else 'English'


def canonical_language(value: str, text: str = '') -> str:
    return ALIASES.get(value.strip().lower(), value.strip()) if value.strip() else content_language(text)


def delivery_language(context: str, text: str) -> str:
    hits = list(re.finditer(r'普通话|中文|汉语|英语|英文|\b(?:Mandarin|Chinese|English)\b', context, re.I))
    return canonical_language(hits[-1].group()) if hits else content_language(text)


def prepare(brief: Brief) -> Brief:
    """Run at the shared compile boundary, for HTTP, CLI and in-process callers alike."""
    lines = [replace(d, language=canonical_language(d.language or '', d.text)) for d in brief.dialogue]
    known = {d.text for d in lines}
    extracted = []
    for m, text in quoted(brief.intent):
        if not text or text in known:
            continue
        before = brief.intent[:m.start()]
        # Only the current clause can classify a quotation; an earlier person's utterance
        # must not license a later sign as dialogue.
        context = re.split(r'[。！？\n.!?]', before)[-1][-160:]
        speech = list(SPEECH.finditer(context))
        lettering = list(LETTERING.finditer(context))
        if not speech or (lettering and lettering[-1].start() > speech[-1].start()):
            continue
        trigger = context[:speech[-1].end()]
        if re.search(r'(?:不要|不许|禁止|无需)\s*(?:说|口播|朗读)|\b(?:do not|don.t|never)\s+(?:say|speak)\b', trigger, re.I):
            continue
        speaker = re.search(r'(?:<Picture\s+(\d+)>|图\s*(\d+)|(甲|乙|丙)|\b(S\d+)\b)', context)
        hint = speaker.group() if speaker else None
        extracted.append(DialogueLine(text=text, language=delivery_language(context, text), speaker_hint=hint,
                                      voiceover=bool(re.search(r'旁白|画外音|voiceover', context, re.I))))
        known.add(text)
    lines.extend(extracted)
    # Explicit language in the intent also applies to structured lines lacking a language.
    for i, d in enumerate(brief.dialogue):
        if not d.language:
            lines[i] = replace(lines[i], language=delivery_language(brief.intent, d.text))
    return replace(brief, dialogue=lines, description_language=description_language(brief.intent))


def protected_intent(brief: Brief) -> str:
    tokens={d.text:'{{D%d}}'%i for i,d in enumerate(brief.dialogue,1)}
    text=QUOTES.sub(lambda m:tokens.get(next(x for x in m.groups() if x is not None),m.group()), brief.intent)
    for literal,token in tokens.items():text=text.replace(literal,token)
    return text


def restore_dialogue(text: str, brief: Brief, speakers=None) -> str:
    from .render import dialogue_markup
    if speakers is None:
        from .plan import build_speakers
        speakers = build_speakers(brief, [], [])
    for i, line in enumerate(brief.dialogue, 1):
        speaker = next((s for s in speakers if s.descriptor == (line.speaker_hint or "")), None)
        text = text.replace('{{D%d}}' % i, dialogue_markup(line, speaker, brief.description_language))
    return text


def prompt_policy(system: str, brief: Brief) -> str:
    language = brief.description_language or description_language(brief.intent)
    if language == 'Chinese':
        system = re.sub(r'Write in English', 'Write descriptive prose in Chinese', system, flags=re.I)
        system = re.sub(r'(\d[–-]\d) English sentences', r'\1 Chinese sentences', system)
        system = system.replace('natural English', 'natural Chinese').replace('English sentences', 'Chinese sentences')
    from pathlib import Path
    policy = (Path(__file__).parent / 'prompts' / 'language_policy.v1.txt').read_text(encoding='utf-8')
    return system + '\n\n' + policy.replace('{language}', language)



def without_protected(text: str) -> str:
    text = re.sub(r'<d>.*?</d>', '', text, flags=re.S)
    return QUOTES.sub('', text)


def language_findings(text: str, language: str, dialogue=()) -> list[Finding]:
    findings = []
    # Audit the actual prose fields, not header names or the English FL2VA protocol line.
    for name in ('subject_definitions','summary','retention_analysis','detailed_description', 'integrated_multimodal_description', 'overall_soundscape', 'non_diegetic_music'):
        match = re.search(r'(?:^|\n)'+name+r':\s*(.*?)(?=\n(?:[a-z_]+):|\Z)', text, re.S)
        if not match:
            continue
        body = without_protected(match.group(1))
        body = re.sub(r'<[^>]+>|\[Shot\s+\d+\]|At\s+\d+:\d+\.\d+|\bPicture\s+\d+', '', body)
        body = re.sub(r'\[[^\]]+\]|\b(?:appears in|fully_preserved|partially_preserved|weak_reference|attribute_transfer|fully_copy|reference)\b', '', body)
        body = body.replace('N/A', '').strip()
        if not body:
            continue
        han = len(HAN.findall(body)); words = len(re.findall(r'[A-Za-z]+', body))
        if language == 'Chinese' and (han == 0 or words > han):
            findings.append(Finding('DINGO-description-language', 'ERROR', f'{name} must use Chinese descriptive prose'))
        elif language == 'English' and han > max(words, 2):
            findings.append(Finding('DINGO-description-language', 'ERROR', f'{name} must use English descriptive prose'))
    blocks = re.findall(r'<d>\s*\[([^\]]+)\]\s*(.*?)</d>', text, re.S)
    for expected_text, expected_language in dialogue:
        for actual_language, actual_text in blocks:
            if expected_text in actual_text and canonical_language(actual_language) != canonical_language(expected_language):
                findings.append(Finding('DINGO-dialogue-language', 'ERROR', f'spoken line must be tagged [{expected_language}], not [{actual_language}]'))
    return findings


CAMERA_ZH = {
 'Zoom In':'镜头变焦拉近','Zoom Out':'镜头变焦拉远','Push In':'镜头向前推进',
 'Pull Out':'镜头向后拉远','Pan Left':'镜头向左摇摄','Pan Right':'镜头向右摇摄',
 'Truck Left':'镜头向左平移','Truck Right':'镜头向右平移','Tilt Up':'镜头向上俯仰',
 'Tilt Down':'镜头向下俯仰','Pedestal Up':'镜头垂直升高','Pedestal Down':'镜头垂直降低',
 'Arc Shot':'镜头环绕主体','Tracking Shot':'镜头跟随主体','Static Shot':'镜头保持固定',
 'Shake Slightly':'镜头轻微晃动','Shake Strongly':'镜头强烈晃动','POV':'镜头采用主观视角',
 'Roll Clockwise':'镜头顺时针旋转','Roll Counterclockwise':'镜头逆时针旋转',
}


def draft_intent(brief: Brief) -> str:
    """Speech is rendered from the typed channel, never echoed as quoted scene lettering."""
    return re.sub(r'\{\{D\d+\}\}', '', protected_intent(brief))
