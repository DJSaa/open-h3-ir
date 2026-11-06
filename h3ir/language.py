"""Official English description policy and byte-preserving speech extraction.

Descriptions use English regardless of the input language. Speech is a separate
channel: its language comes from the explicit delivery request, otherwise from the line.
Nothing in this module translates user text or guesses spoken content from audio.
"""
from __future__ import annotations

import re
from dataclasses import replace
from .models import Brief, DialogueLine, Finding

POLICY_VERSION = "dingo-language-v3"
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
    """Follow the official H3 description language; never translate literal speech."""
    return 'English'



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


# Only immediate speech introductions are consumed. Never match across a sentence,
# shot boundary, dialogue block or an arbitrary action clause.
_SPEAKER_ATOM = r'(?:<Subject\s+\d+>(?:\s+\(S\d+\))?|\(S\d+\))'
_VOICE_CLAUSE = r'(?:\s*,?\s*using the voice timbre referenced from <Audio\s+\d+>\s*,?)?'
_DELIVERY = r'(?:(?:softly|quietly|gently|calmly)\s+)?(?:says|speaks|replies|responds|whispers|shouts)(?:\s+(?:in an? off-screen voiceover|off-screen|softly|quietly|gently|in a whisper))?'


def _restore_token(text, token, markup, speaker):
    """Replace one compiler-owned token and any redundant structured introduction.

    Keep model-written delivery modifiers. Unknown prose is not deleted: the final
    validator can send a remaining duplicate to the existing correction pass.
    """
    identities = [_SPEAKER_ATOM, r'\b(?:[Ss]he|[Hh]e|[Tt]hey|[Tt]he speaker)\b']
    if speaker and speaker.descriptor:
        hint = re.escape(speaker.descriptor)
        identities.insert(0, hint + r'(?:\s+' + _SPEAKER_ATOM + r')?')
    intro = re.compile(r'(?<!\w)(?:' + '|'.join(identities) + r')' + _VOICE_CLAUSE
                       + r'\s+(?P<delivery>' + _DELIVERY + r')\s*[,：:]?\s*$', re.I)
    voice_intro = re.compile(r'(?<!\w)(?:' + '|'.join(identities) + r')'
                             r'\s+uses the (?P<quality>(?:[a-z-]+\s+){0,5})voice timbre '
                             r'referenced from <Audio\s+\d+> to (?:say|speak)\s*[,：:]?\s*$', re.I)

    def introduction(prefix):
        return intro.search(prefix) or voice_intro.search(prefix)

    def insert(match):
        prefix = match.group('prefix')
        found = introduction(prefix)
        if not found:
            return prefix + markup
        delivery = found.groupdict().get('delivery') or 'says'
        # Repeated adjacent introductions are all wrappers of this one token.
        start = found.start()
        while previous := introduction(prefix[:start].rstrip()):
            start = previous.start()
        rendered = markup
        if ' says:' in rendered:
            rendered = rendered.replace(' says:', ' ' + delivery + ':', 1)
        quality = found.groupdict().get('quality')
        if quality:
            rendered = rendered.replace('using the voice timbre',
                                        'using the ' + quality + 'voice timbre', 1)
        # A pronoun is useful in a base-mode request with no resolved subject.
        lead = found.group().split()[0]
        if rendered.startswith('(S') and lead.lower() in {'she', 'he', 'they'}:
            rendered = lead + ' ' + rendered
        return prefix[:start] + rendered

    # Work only in text segments preceding this token, never inside protected speech.
    parts = re.split(r'(<d>.*?</d>)', text, flags=re.S)
    for n in range(0, len(parts), 2):
        parts[n] = re.sub(r'(?P<prefix>[^\n]*?)' + re.escape(token), insert, parts[n])
    return ''.join(parts)


def restore_dialogue(text: str, brief: Brief, speakers=None) -> str:
    from .render import dialogue_markup
    # A writer may ignore the no-wrapper instruction. Never paste a full speech
    # construct inside model-owned <d>: that turns delivery directions into speech.
    def unwrap(match):
        tokens = re.findall(r'\{\{D\d+\}\}', match.group())
        return ' '.join(tokens) if tokens else match.group()
    text = re.sub(r'<d>.*?</d>', unwrap, text, flags=re.S)
    if speakers is None:
        from .plan import build_speakers
        speakers = build_speakers(brief, [], [])
    for i, line in enumerate(brief.dialogue, 1):
        speaker = next((s for s in speakers if s.descriptor == (line.speaker_hint or "")), None)
        text = _restore_token(text, '{{D%d}}' % i,
                              dialogue_markup(line, speaker, brief.description_language), speaker)
    return text


def prompt_policy(system: str, brief: Brief) -> str:
    from pathlib import Path
    policy = (Path(__file__).parent / 'prompts' / 'language_policy.v2.txt').read_text(encoding='utf-8')
    speech = (Path(__file__).parent / 'prompts' / 'dialogue_review.v5.txt').read_text(encoding='utf-8')
    return system + '\n\n' + policy + '\n\n' + speech



def without_protected(text: str) -> str:
    text = re.sub(r'<d>.*?</d>', '', text, flags=re.S)
    # Official H3 permits original-language *visible lettering*, not every quoted
    # instruction. A bare quote around 图1 or a stage direction must still be audited.
    def visible(match):
        before = text[:match.start()]
        clause = re.split(r'[.!?。！？\n;]', before)[-1]
        cue = re.search(r'\b(?:sign|caption|subtitle|lettering|on-screen text|onscreen text|'
                        r'screen displays?|text (?:reads|says)|words? (?:written|printed))\b',
                        clause, re.I)
        return '' if cue else match.group()
    return QUOTES.sub(visible, text)


def language_findings(text: str, language: str, dialogue=()) -> list[Finding]:
    findings = []
    depth = 0
    for tag in re.finditer(r'</?d>', text):
        if tag.group() == '<d>':
            if depth:
                findings.append(Finding('DINGO-nested-dialogue', 'ERROR', 'dialogue blocks must not be nested'))
            depth += 1
        else:
            depth = max(0, depth - 1)
    # Audit the actual prose fields, not header names or the English FL2VA protocol line.
    for name in ('subject_definitions','summary','retention_analysis','detailed_description', 'integrated_multimodal_description', 'overall_soundscape', 'non_diegetic_music'):
        match = re.search(r'(?:^|\n)'+name+r':\s*(.*?)(?=\n(?:[a-z_]+):|\Z)', text, re.S)
        if not match:
            continue
        body = without_protected(match.group(1))
        body = re.sub(r'<(?:Subject|Picture|Video|Audio)\s+\d+>|<scenetrans>|\[Shot\s+\d+\]|At\s+\d+:\d+\.\d+|\bPicture\s+\d+', '', body)
        body = re.sub(r'\b(?:appears in|fully_preserved|partially_preserved|weak_reference|attribute_transfer|fully_copy|reference)\b', '', body)
        body = body.replace('N/A', '').strip()
        if not body:
            continue
        han = len(HAN.findall(body)); words = len(re.findall(r'[A-Za-z]+', body))
        if language == 'Chinese' and (han == 0 or words > han):
            findings.append(Finding('DINGO-description-language', 'ERROR', f'{name} must use Chinese descriptive prose'))
        elif language == 'English' and han:
            findings.append(Finding('DINGO-description-language', 'ERROR', f'{name} must use English descriptive prose'))
    # A single utterance must not have two nested speech introductions. Repeated
    # appearances of the same speaker elsewhere are legitimate and are untouched.
    duplicate = re.compile(r'(?:' + _DELIVERY + r'|to say|to speak|delivers? the line|delivering(?: the line)?|finishing the sentence)'
                           + r'\s*[,：:]?\s*' + _SPEAKER_ATOM + _VOICE_CLAUSE
                           + r'\s+' + _DELIVERY + r'\s*[,：:]?\s*<d>', re.I)
    prose_only = re.sub(r'<d>.*?</d>', '<d>', text, flags=re.S)
    # Same speaker is introduced twice for ONE following utterance, separated by
    # a mouth-articulation aside. Do not cross sentences, shots or dialogue blocks,
    # and do not consume arbitrary actions or another speaker's introduction.
    articulated_duplicate = re.compile(
        r'(?P<speaker><Subject\s+\d+>(?:\s+\(S\d+\))?|\(S\d+\))'
        + _VOICE_CLAUSE + r'\s+(?:responds|replies|says|speaks)\s*,\s*'
        r'(?:his|her|their)\s+lips\s+(?:forming|shaping|articulating)\s+'
        r'the\s+words(?:\s+(?:clearly|carefully|slowly|softly))?\s*,\s*'
        r'(?P=speaker)' + _VOICE_CLAUSE + r'\s+' + _DELIVERY + r'\s*[,：:]?\s*<d>', re.I)
    if duplicate.search(prose_only) or articulated_duplicate.search(prose_only):
        findings.append(Finding('DINGO-duplicate-speech-prefix', 'ERROR',
                                'one dialogue block has repeated speaker introductions; '
                                'keep a single speaker, delivery and voice binding before <d>'))
    blocks = re.findall(r'<d>\s*\[([^\]]+)\]\s*(.*?)</d>', text, re.S)
    for expected_text, expected_language in dialogue:
        for actual_language, actual_text in blocks:
            if expected_text in actual_text and canonical_language(actual_language) != canonical_language(expected_language):
                findings.append(Finding('DINGO-dialogue-language', 'ERROR', f'spoken line must be tagged [{expected_language}], not [{actual_language}]'))
    return findings


def draft_intent(brief: Brief) -> str:
    """Speech is rendered from the typed channel, never echoed as quoted scene lettering."""
    return re.sub(r'\{\{D\d+\}\}', '', protected_intent(brief))
