from h3ir.models import Brief, DialogueLine
from h3ir.dialogue_schedule import evidence, guidance, findings
from h3ir.prose import fix_with_findings
from tests.test_dialogue_review import Capture, Pipeline, compile_fake

LINE = "周末一起去看海，我们早点出发。"

def test_duration_evidence_does_not_stretch_with_video_or_expose_speech():
    a = Brief(intent="test", seconds=5, dialogue=[DialogueLine(LINE, language="Chinese")])
    b = Brief(intent="test", seconds=15, dialogue=a.dialogue)
    assert evidence(a)["utterances"] == evidence(b)["utterances"]
    assert evidence(b)["utterances"][0]["ordinary_speech_seconds"][1] < 6
    assert LINE not in guidance(b)
    assert evidence(b)["video_seconds"] == 15
    assert guidance(Brief(intent="silent")) == ""

def test_correction_gets_duration_and_preserves_pinned_timing():
    b = Brief(intent="At 6.5 seconds cut; speak slowly", seconds=15, dialogue=[DialogueLine(LINE)])
    backend = Capture()
    fix_with_findings(backend, "draft", [], brief=b, review=True)
    ask = backend.calls[0][0][1]["content"]
    assert '"video_seconds": 15' in ask
    assert 'At 6.5 seconds cut; speak slowly' in ask
    assert 'Keep caller-pinned timing' in ask
    assert 'immediately' in ask and LINE not in ask

def test_out_of_range_header_only_and_advisory_rate():
    b = Brief(intent="test", seconds=5, dialogue=[DialogueLine(LINE)])
    bad = findings('[Shot 2] At 00:06.500, he replies.', b)
    assert len(bad) == 1 and bad[0].severity == 'ERROR'
    assert not findings('[Shot 2] At 00:04.500, he replies.', b)
    assert not findings('[Shot 1] A clock reads At 00:06.500.', b)
    b.seconds = 15
    assert not findings('[Shot 2] At 00:06.500, he replies.', b)

def test_both_model_calls_and_shipped_provenance_receive_evidence(monkeypatch):
    backend = Pipeline()
    doc = compile_fake(monkeypatch, backend)
    assert doc.source == 'written'
    assert len(backend.calls) == 2
    assert all('SPEECH SCHEDULING EVIDENCE' in str(call) for call in backend.calls)
    assert doc.provenance['dialogue_schedule'].get('version') == 'dialogue-schedule-v2'
    assert doc.provenance['dialogue_schedule']['utterances']


def test_source_audio_duration_is_not_target_speech_duration():
    b = Brief(intent='test', seconds=15, dialogue=[DialogueLine(LINE)])
    text = guidance(b)
    assert 'SOURCE audio only' in text and 'not to explicitly requested audio reuse' in text
