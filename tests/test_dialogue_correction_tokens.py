"""Correction must preserve caller words without preserving duplicate occurrences."""
import re
from types import SimpleNamespace
from h3ir.models import Brief, DialogueLine, Finding
from h3ir.prose import fix_with_findings

LINE = "周末一起去看海，我们早点出发。"
SECOND = "好，我来准备。"

class Backend:
    def __init__(self, output):
        self.output = output
        self.messages = None
    def chat(self, messages, **kwargs):
        self.messages = messages
        return SimpleNamespace(content=self.output)

def run(output, line=LINE, language="Chinese"):
    b = Backend(output)
    brief = Brief(intent="A speaks across a cut; B replies.", dialogue=[
        DialogueLine(text=line, language=language), DialogueLine(text=SECOND, language="Chinese")])
    block = f"<d>[{language}] {line}</d>"
    written = f"[Shot 1] (S1) {block}\n[Shot 2] (S1) continues off-screen {block} then (S2) <d>[Chinese] {SECOND}</d>"
    result = fix_with_findings(b, written, [Finding("D10-dialogue-line-duplicated", "ERROR", f"line {line} appears twice")], brief=brief)
    return b, result

def test_identical_speech_occurrences_have_one_identity():
    b, _ = run("{{UTTERANCE1}} {{UTTERANCE2}}")
    original = b.messages[-1]["content"].split("YOUR TEXT\n", 1)[1]
    assert original.count("{{UTTERANCE1}}") == 2
    assert original.count("{{UTTERANCE2}}") == 1
    assert "{{UTTERANCE3}}" not in original
    assert LINE not in str(b.messages)
    assert "remove duplicate occurrences" in str(b.messages)

def test_chinese_split_across_cut_restores_exact_words_once():
    b, text = run("[Shot 1] (S1) {{UTTERANCE1_PART1}} <scenetrans> The audio continues across the cut.\n[Shot 2] <scenetrans> (S1) continues off-screen {{UTTERANCE1_PART2}} then (S2) {{UTTERANCE2}}")
    assert "{{UTTERANCE1_PART1}}" in str(b.messages)
    assert "{{UTTERANCE1_PART2}}" in str(b.messages)
    blocks = re.findall(r"<d>\[Chinese\] (.*?)</d>", text)
    assert blocks == ["周末一起去看海，", "我们早点出发。", SECOND]
    assert "".join(blocks[:2]) == LINE
    assert text.count("<scenetrans>") == 2
    assert "{{" not in text

def test_english_split_keeps_language_and_punctuation():
    _, text = run("{{UTTERANCE1_PART1}} <scenetrans> [Shot 2] <scenetrans> {{UTTERANCE1_PART2}} {{UTTERANCE2}}", line="Ready, let's go.", language="English")
    blocks = re.findall(r"<d>\[English\] (.*?)</d>", text)
    assert "".join(blocks) == "Ready, let's go."
    assert len(blocks) == 2
    assert "{{" not in text

def test_short_unsplit_line_can_be_kept_once():
    _, text = run("{{UTTERANCE1}} {{UTTERANCE2}}", line="走吧。")
    assert text.count("走吧。") == 1
    assert text.count(SECOND) == 1

def test_duplicate_is_still_rejected_if_model_keeps_it():
    from h3ir.validate import Context, validate
    _, text = run("[Shot 1] {{UTTERANCE1}} [Shot 2] At 00:04.000, {{UTTERANCE1}} {{UTTERANCE2}}")
    document = "integrated_multimodal_description: " + text + "\n\noverall_soundscape: Quiet.\n\nnon_diegetic_music: N/A"
    assert "D10-dialogue-line-duplicated" in {f.rule for f in validate(document, Context(mode="t2va", n_pictures=0, duration_s=8, expected_dialogue=(LINE, SECOND)))}


def test_model_wrapper_does_not_nest_speech_blocks():
    _, text = run("<d>[Chinese] {{UTTERANCE1_PART1}}</d> <scenetrans> [Shot 2] <scenetrans> <d>{{UTTERANCE1_PART2}}</d> {{UTTERANCE2}}")
    assert text.count("<d>") == 3
    assert "<d>[Chinese] <d>" not in text
    assert "{{" not in text


def test_corrected_chinese_split_survives_repair_and_validation():
    from h3ir.grid import Target
    from h3ir.models import Mode
    from h3ir.repair import repair
    from h3ir.validate import Context, validate
    _, text = run('[Shot 1] A realistic office. Static Shot. (S1) {{UTTERANCE1_PART1}} <scenetrans> The audio continues seamlessly across the cut.\n[Shot 2] At 00:04.000, <scenetrans> (S1) continues off-screen {{UTTERANCE1_PART2}} then (S2) {{UTTERANCE2}}')
    document = 'integrated_multimodal_description: ' + text + '\n\noverall_soundscape: Quiet.\n\nnon_diegetic_music: N/A'
    fixed = repair(document, target=Target.build(8), mode=Mode.T2VA, labels=(), dialogue=(LINE, SECOND))
    blocks = re.findall(r'<d>\[Chinese\] (.*?)</d>', fixed.text)
    assert blocks == ['周末一起去看海，', '我们早点出发。', SECOND]
    errors = [f for f in validate(fixed.text, Context(mode='t2va', n_pictures=0, duration_s=Target.build(8).effective_seconds, expected_dialogue=(LINE, SECOND))) if f.severity=='ERROR']
    assert not errors, errors


def test_chinese_split_verbatim_still_rejects_missing_or_reordered_words():
    from h3ir.validate import _consecutive_run
    assert _consecutive_run(['周末一起去看海，', '我们早点出发。'], LINE) == (0, 1)
    assert _consecutive_run(['周末一起去看海，', '我们出发。'], LINE) is None
    assert _consecutive_run(['我们早点出发。', '周末一起去看海，'], LINE) is None
