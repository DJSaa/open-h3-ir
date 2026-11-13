"""Official English description policy and byte-preserving speech extraction.

Descriptions use English regardless of the input language. Speech is a separate
channel: its language comes from the explicit delivery request, otherwise from the line.
Nothing in this module translates user text or guesses spoken content from audio.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, replace
from collections import Counter, defaultdict, deque
from .models import Brief, DialogueLine, Finding

POLICY_VERSION = "dingo-language-v4"
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


@dataclass(frozen=True)
class _SpeechOccurrence:
    start: int
    end: int
    line: DialogueLine
    count: int = 1


def _negated_speech(context: str, verb_start: int) -> bool:
    """A local negator governs THIS speech verb, not a previous quoted sentence.

    Causative/prepositional subjects are allowed between the negator and verb.
    A comma or contrast starts a new clause; 'do not worry, say ...' stays positive.
    Unrecognised prose is left intact for the writer, never silently rewritten.
    """
    prefix = re.split(r'[,，;；]', context[:verb_start])[-1]
    negator = r'(?:不要|不许|禁止|无需|不能|不得|别)'
    subject = r'(?:(?:让|叫|使|要求|允许|对|向|跟)\s*[^，,;；。！？.!?]{1,16}?|(?:他|她|他们|她们|人物|角色|任何人))?'
    if re.search(negator + r'\s*' + subject + r'\s*(?:再|去|开口|直接|大声|小声)?\s*$', prefix):
        return True
    return bool(re.search(
        r"\b(?:do\s+not|does\s+not|don['’]t|must\s+not|mustn['’]t|should\s+not|never|without)\s+"
        r'(?:(?:let|make|have|allow)\s+(?:him|her|them|the\s+(?:person|character))\s+(?:to\s+)?)?'
        r'(?:(?:ever|again|aloud)\s+)*$', prefix, re.I))


def _repeat_count(context: str, tail: str) -> int:
    # Explicit adjacent counts only. A second quoted utterance is a separate event,
    # not another count to apply to the preceding event.
    numbers = {'一':1, '二':2, '两':2, '三':3, '四':4, '五':5,
               '六':6, '七':7, '八':8, '九':9, '十':10}
    for is_tail, part in ((True, tail), (False, context)):
        pat = (r'^\s*(?:重复|连说|说)?\s*(\d{1,3}|[一二两三四五六七八九十])\s*(?:次|遍)'
               if is_tail else
               r'(?:说|重复|连说)\s*(\d{1,3}|[一二两三四五六七八九十])\s*(?:次|遍)\s*[：:]?\s*$')
        m = re.search(pat, part)
        if m:
            return max(1, int(m[1]) if m[1].isdigit() else numbers[m[1]])
    m = re.match(r'\s*(once|twice|three\s+times|\d{1,3}\s+times)\b', tail, re.I)
    if m:
        word = m[1].lower()
        return {'once':1, 'twice':2, 'three times':3}.get(word, int(word.split()[0]) if word[0].isdigit() else 1)
    return 1


def _speech_occurrences(intent: str) -> list[_SpeechOccurrence]:
    """Positive speech occurrences with source spans; repeated text is NOT deduped."""
    from .structured_input import SPEECH as NATIVE_SPEECH
    native = list(NATIVE_SPEECH.finditer(intent))
    occurrences = []
    for m in native:
        literal = m[2].strip()
        if not literal:
            continue
        before = intent[:m.start()].split('\n')[-1]
        speakers = list(re.finditer(r'\bS\d+\b', before))
        occurrences.append(_SpeechOccurrence(m.start(2), m.end(2),
            DialogueLine(literal, canonical_language(m[1], literal),
                         speakers[-1].group() if speakers else None)))
    # Quote punctuation inside a prior utterance is not a sentence boundary or speaker cue.
    scan = list(intent)
    for m, _ in quoted(intent):
        scan[m.start():m.end()] = ' ' * (m.end()-m.start())
    scan = ''.join(scan)
    previous_quote_end = 0
    for m, literal in quoted(intent):
        clause_start = previous_quote_end
        previous_quote_end = m.end()
        if not literal or any(n.start() <= m.start() < n.end() for n in native):
            continue
        context = re.split(r'[。！？\n.!?]', scan[clause_start:m.start()])[-1][-160:]
        speech = list(SPEECH.finditer(context)); lettering = list(LETTERING.finditer(context))
        if not speech or (lettering and lettering[-1].start() > speech[-1].start()):
            continue
        verb = speech[-1]
        if _negated_speech(context, verb.start()):
            continue
        speakers = list(re.finditer(r'(?:<Picture\s+(\d+)>|图\s*(\d+)|(甲|乙|丙)|\b(S\d+)\b)', context))
        hint = speakers[0].group() if speakers else None
        occurrences.append(_SpeechOccurrence(m.start(), m.end(),
            DialogueLine(literal, delivery_language(context, literal), hint,
                         bool(re.search(r'旁白|画外音|voiceover', context, re.I))),
            _repeat_count(context, intent[m.end():])))
    return sorted(occurrences, key=lambda o:o.start)


def prepare(brief: Brief) -> Brief:
    """Merge explicit metadata and inline occurrences without losing multiplicity.

    Metadata and prose may describe the SAME speech, so use occurrence counts instead
    of adding both sources. Each independently requested utterance keeps its own entry.
    """
    lines = [replace(d, language=canonical_language(d.language or '', d.text)) for d in brief.dialogue]
    explicit = Counter(d.text for d in lines)
    seen = Counter()
    for occurrence in _speech_occurrences(brief.intent):
        line = occurrence.line
        # An explicit metadata/inline language disagreement must still be refused.
        if brief.dialogue and any(d.text == line.text for d in lines):
            if any(d.text == line.text and d.language and canonical_language(d.language, d.text) != line.language for d in brief.dialogue):
                from .compile import BriefRefused
                raise BriefRefused("input-dialogue-conflict", "The explicit dialogue and H3 text specify different languages for the same line.")
        for _ in range(occurrence.count):
            seen[line.text] += 1
            if seen[line.text] > explicit[line.text]:
                lines.append(line)
    for i, d in enumerate(brief.dialogue):
        if not d.language:
            lines[i] = replace(lines[i], language=delivery_language(brief.intent, d.text))
    return replace(brief, dialogue=lines, description_language=description_language(brief.intent))


def protected_intent(brief: Brief) -> str:
    counts = Counter(d.text for d in brief.dialogue)
    if all(n == 1 for n in counts.values()):
        # Preserve the established single-utterance path byte-for-byte.
        tokens = {d.text:'{{D%d}}'%i for i,d in enumerate(brief.dialogue,1)}
        text = QUOTES.sub(lambda m:tokens.get(next(x for x in m.groups() if x is not None),m.group()), brief.intent)
        for literal, token in tokens.items():
            text = text.replace(literal, token)
        return text
    tokens = defaultdict(deque)
    for i, line in enumerate(brief.dialogue, 1):
        tokens[line.text].append('{{D%d}}'%i)
    replacements = []
    for occurrence in _speech_occurrences(brief.intent):
        available = tokens[occurrence.line.text]
        if available:
            replacements.append((occurrence.start, occurrence.end, available.popleft()))
    text = brief.intent
    for start, end, token in reversed(replacements):
        text = text[:start] + token + text[end:]
    # Explicit API metadata can accompany unquoted prose. Protect those literal
    # mentions too, without substituting inside already-assigned event tokens.
    for literal in sorted(tokens, key=len, reverse=True):
        fallback = '{{D%d}}' % next(i for i,d in enumerate(brief.dialogue,1) if d.text == literal)
        def hide(match):
            if match.group(1):
                return match.group()
            return tokens[literal].popleft() if tokens[literal] else fallback
        text = re.sub(r'(\{\{D\d+\}\})|' + re.escape(literal), hide, text)
    return text


def explicit_no_speech(brief: Brief) -> bool:
    """Only a global speech prohibition; never infer all-audio silence from it."""
    if brief.dialogue:
        return False
    return bool(re.search(r'(?:静默|无声)(?:视频|片段)|不说话|(?:无|没有|禁止)(?:对白|台词|旁白)|'
                          r'(?:不要|不能|不许)(?:有|出现|添加)?任何(?:说话声音|话语|对白)', brief.intent))


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


def _speech_references_outside_shots(text: str, brief: Brief) -> str:
    """Only shot descriptions schedule the protected speech events.

    A generated summary may repeat a protected token to describe the plot. Restoring
    that mention as another <d> block duplicates the event. Keep it as a prose
    reference instead. Unknown/generated speech is NOT removed: verification must
    still report it. Missing speech in the actual shots still fails D4.
    """
    from .structured_input import NAMES
    headers = list(re.finditer(r'(?m)^(' + '|'.join(NAMES) + r')\s*:', text))
    if not headers or not brief.dialogue:
        return text
    expected = {d.text.strip() for d in brief.dialogue}
    out = [text[:headers[0].start()]]
    for i, header in enumerate(headers):
        end = headers[i+1].start() if i+1 < len(headers) else len(text)
        segment = text[header.start():end]
        if header[1] not in ('detailed_description','integrated_multimodal_description'):
            token = '{{SPEECH_REFERENCE}}'
            def reference(match):
                return token if match[1].strip() in expected else match.group()
            segment = re.sub(r'<d>\s*\[[^]]+\]\s*(.*?)</d>', reference, segment, flags=re.S)
            segment = _restore_token(segment, token, 'a supplied utterance', None)
        out.append(segment)
    return ''.join(out)


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
    return _speech_references_outside_shots(text, brief)


def prompt_policy(system: str, brief: Brief) -> str:
    from pathlib import Path
    policy = (Path(__file__).parent / 'prompts' / 'language_policy.v2.txt').read_text(encoding='utf-8')
    speech = (Path(__file__).parent / 'prompts' / 'dialogue_review.v5.txt').read_text(encoding='utf-8')
    input_policy = (Path(__file__).parent / 'prompts' / 'input_understanding.v1.txt').read_text(encoding='utf-8')
    import json
    requirements = ("\n\nEVIDENCE-BACKED REQUIREMENTS (original request remains authoritative):\n" + json.dumps(brief.requirements_context, ensure_ascii=False)) if brief.requirements_context else ""
    repeated = Counter(d.text for d in brief.dialogue)
    if any(n > 1 for n in repeated.values()):
        events = ', '.join('{{D%d}}'%i for i in range(1, len(brief.dialogue)+1))
        requirements += ('\nAUTHORIZED SPEECH EVENTS: ' + events +
            '. Each entry is one independent requested utterance, used once, in order. '
            'Identical words in different entries are authorized repetitions, not duplicates. '
            'Repetition counts in the original request are already expanded into these entries; '
            'do not multiply them again. Preserve requested pauses and speakers.')
    return system + '\n\n' + policy + '\n\n' + speech + '\n\n' + input_policy + requirements



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
