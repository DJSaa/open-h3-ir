from types import SimpleNamespace
import pytest
from h3ir.models import Brief, DialogueLine, Mode
from h3ir.language import prepare, restore_dialogue
from h3ir.plan import build_speakers
from h3ir.voice_binding import findings

ROLES=(('<Audio 1>','voice_timbre'),('<Audio 2>','voice_timbre'))

def speakers(intent, roles=ROLES, dialogue=None):
    b=prepare(Brief(intent=intent,dialogue=dialogue or []))
    return b,build_speakers(b,[],[],audio_roles=roles)

@pytest.mark.parametrize('intent,expected',[
 ('图1说：“周末一起去看海，我们早点出发。”图2接着说：“好，我来准备。”两人的声音分别参考音频1、音频2。',['<Audio 1>','<Audio 2>']),
 ('图1说：“甲。”图2说：“乙。”两人的声音分别参考音频2、音频1。',['<Audio 2>','<Audio 1>']),
 ('甲只借<Audio 1>的音色，乙只借<Audio 2>的音色。甲说：“你好。”乙回答：“好。”',['<Audio 1>','<Audio 2>']),
 ('甲用音频2的声音，乙用音频1的声音。乙说：“你好。”甲说：“好。”',['<Audio 1>','<Audio 2>']),
 ('甲用音频2的音色，乙用音频2的音色。甲说：“你好。”乙说：“好。”',['<Audio 2>','<Audio 2>']),
 ('甲说：“你好。”乙说：“好。”',[None,None]),
])
def test_explicit_bindings(intent,expected):
    b,sp=speakers(intent)
    assert [x.voice_ref for x in sp]==expected
    text=restore_dialogue('[Shot 1] {{D1}} [Shot 2] {{D2}}',b,sp)
    for i,voice in enumerate(expected,1):
        if voice is not None:assert f'(S{i}), using the voice timbre referenced from {voice},' in text
    assert not findings(text,sp)

def test_music_not_bound_and_single_voice_compatible():
    b,sp=speakers('她说：“你好。”',roles=(('<Audio 1>','bgm'),('<Audio 2>','voice_timbre')))
    assert sp[0].voice_ref=='<Audio 2>'
    _,sp=speakers('她说：“你好。”',roles=(('<Audio 1>','bgm'),))
    assert sp[0].voice_ref is None

def test_repeated_speaker_has_stable_voice():
    b,sp=speakers('甲用音频2的音色。',dialogue=[DialogueLine('你好',speaker_hint='甲'),DialogueLine('再见',speaker_hint='甲')])
    assert len(sp)==1 and sp[0].voice_ref=='<Audio 2>'
    assert restore_dialogue('{{D1}} {{D2}}',b,sp).count('referenced from <Audio 2>')==2

def test_ambiguous_or_invalid_binding_is_not_guessed():
    for intent in ['甲用音频1。甲用音频2。','甲用音频3。','甲不要使用音频1。']:
        _,sp=speakers(intent,dialogue=[DialogueLine('你好',speaker_hint='甲')])
        assert sp[0].voice_ref is None

def test_final_prose_conflict_detected_and_quoted_labels_ignored():
    _,sp=speakers('甲用音频1，乙用音频2。',dialogue=[DialogueLine('你好',speaker_hint='甲'),DialogueLine('好',speaker_hint='乙')])
    wrong='detailed_description: [Shot 1] (S2), using the voice timbre referenced from <Audio 1>, says: <d>[Chinese] 好</d>.'
    assert any(f.rule=='DINGO-voice-binding' and f.severity=='ERROR' for f in findings(wrong,sp))
    assert not findings(wrong.replace('from <Audio 1>','from <Audio 2>'),sp)
    assert not findings('(S2) says <d>[Chinese] 请使用<Audio 1>。</d>.',sp)
    assert findings('<Audio 1> is the voice-timbre reference for <Subject 2> (S2).',sp)

def test_writer_restores_independent_voices_and_sends_mapping():
    from h3ir.prose import compose_brief
    from h3ir.grid import Target
    b=prepare(Brief(intent='图1说：“你好。”图2说：“好。”两人的声音分别参考音频1、音频2。'))
    class Writer:
        def chat(self,messages,**kwargs):
            self.messages=messages
            return SimpleNamespace(content='detailed_description: [Shot 1] {{D1}} [Shot 2] {{D2}}')
    w=Writer()
    result=compose_brief(w,b,[],{},Target(15,362,(1344,768)),labels=('<Audio 1>','<Audio 2>'),mode=Mode.REF2VA,audio_roles=ROLES)
    assert '(S1), using the voice timbre referenced from <Audio 1>,' in result
    assert '(S2), using the voice timbre referenced from <Audio 2>,' in result
    assert '图2 (S2) uses <Audio 2>.' in str(w.messages)


def test_final_check_does_not_confuse_listening_with_voice_reference():
    _,sp=speakers('甲用音频1。',dialogue=[DialogueLine('你好',speaker_hint='甲')])
    assert not findings('(S1) listens to <Audio 2>.',sp)


def test_draft_path_uses_same_two_voice_mapping(monkeypatch):
    import h3ir.compile as module
    from h3ir.models import AssetRef,AssetKind,Role,AssetCard
    assets=[];cards={}
    for i in range(2):
        key='person'+str(i)
        assets.append(AssetRef(kind=AssetKind.IMAGE,role=Role.SUBJECT,sha256=key,px=(1344,768)))
        cards[key]=AssetCard(sha256=key,kind=AssetKind.IMAGE,style='Live-action',subjects=[{'kind':'person','descriptor':'person '+str(i),'attributes':['blue shirt']}])
    for i in range(2):
        key='voice'+str(i)
        assets.append(AssetRef(kind=AssetKind.AUDIO,role=Role.VOICE_TIMBRE,sha256=key,seconds=3))
        cards[key]=AssetCard(sha256=key,kind=AssetKind.AUDIO)
    monkeypatch.setattr(module,'analyse_all',lambda *a,**k:cards)
    monkeypatch.setattr(module,'measure_assets',lambda *a:None)
    backend=SimpleNamespace(require_available=lambda:None,server_version=lambda:'test',cfg=SimpleNamespace(model='test'))
    doc=module.compile_brief(Brief(intent='甲只借<Audio 1>的音色，乙只借<Audio 2>的音色。甲说：“周末一起去看海，我们早点出发。”乙回答：“好，我来准备。”',assets=assets,mode=Mode.REF2VA),backend=backend,llm=False)
    assert doc.ok,doc.errors
    assert '(S1), using the voice timbre referenced from <Audio 1>,' in doc.prompt
    assert '(S2), using the voice timbre referenced from <Audio 2>,' in doc.prompt
