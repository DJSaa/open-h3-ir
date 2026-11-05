from dataclasses import replace
import re
import pytest
from h3ir.models import AssetKind, AssetRef, Brief, Role, Mode
from h3ir.reference_roles import resolve_motion_references
from h3ir.repair import repair
from h3ir.grid import Target
from h3ir.prose import structure_facts
from h3ir.validate import Context, validate


def clip(stated=False):
    return AssetRef(kind=AssetKind.VIDEO, role=Role.EDIT_SOURCE, sha256="clip", role_stated=stated)

@pytest.mark.parametrize("intent", [
    "图1照视频1的样子转头，视频只参考转头。",
    "<Video 1>只给甲转头的动作方向，不提供递物动作。",
    "Reference <Video 1> only for subject motion.",
])
def test_explicit_motion_only_prose_resolves_without_mutating_input(intent):
    b=Brief(intent=intent, assets=[clip()]);r=resolve_motion_references(b)
    assert r.assets[0].role is Role.MOTION_REFERENCE
    assert b.assets[0].role is Role.EDIT_SOURCE
    assert r.intent == b.intent and r.assets[0].sha256 == b.assets[0].sha256

@pytest.mark.parametrize("intent", [
    "编辑视频1，把衣服换成红色。", "视频1不仅参考动作，还保留背景。",
    "不要视频只参考转头。", "视频1只参考镜头运动。", "Use <Video 1> for motion.",
])
def test_ambiguous_negative_or_edit_requests_keep_existing_default(intent):
    b=Brief(intent=intent,assets=[clip()]);assert resolve_motion_references(b) is b

def test_explicit_role_wins():
    b=Brief(intent="视频只参考转头。",assets=[clip(True)])
    assert resolve_motion_references(b) is b

def test_multi_video_scoping():
    b=Brief(intent="视频2只参考转头。",assets=[clip(),replace(clip(),sha256="two")])
    assert [a.role for a in resolve_motion_references(b).assets] == [Role.EDIT_SOURCE,Role.MOTION_REFERENCE]
    b.intent="视频只参考转头。";assert resolve_motion_references(b) is b

def test_fact_block_distinguishes_subject_motion_from_camera_and_identity():
    s=structure_facts((("<Video 1>","motion_reference"),))
    assert "SUBJECT-MOTION REFERENCE" in s and "NOT video editing" in s
    assert "Do not define a <Subject N>" in s

def test_retention_cleaning_does_not_renumber_or_change_voice_bindings():
    text="""subject_definitions:
<Subject 2> (S2), voice timbre referenced from <Audio 2>.
retention_analysis:
<Subject 2> (appears in [Shot 1]): fully_preserved - speaker (S2).
<Audio 2>: reference - voice for <Subject 2> (S2).
detailed_description:
[Shot 1] <Subject 2> (S2) says: <d>[Chinese] 先看这里，拿稳一下。</d>
overall_soundscape: Quiet.
non_diegetic_music: N/A
"""
    r=repair(text,target=Target.build(15),mode=Mode.REF2VA,labels=("<Picture 1>","<Audio 1>","<Audio 2>"))
    ret=r.text.split('retention_analysis:')[1].split('detailed_description:')[0]
    assert '(S2)' not in ret and '<Subject 2>' in ret and '<Audio 2>' in ret
    assert r.text.split('retention_analysis:')[0] == text.split('retention_analysis:')[0]
    assert r.text.split('detailed_description:')[1] == text.split('detailed_description:')[1]
    assert repair(r.text,target=Target.build(15),mode=Mode.REF2VA,labels=("<Picture 1>","<Audio 1>","<Audio 2>")).text == r.text

def test_motion_identity_leak_is_rejected_but_edit_source_unchanged():
    text="subject_definitions: \n<Subject 1> is the man from <Video 1>.\nsummary: [reference generation] A man.\nretention_analysis: \n<Subject 1> (appears in [Shot 1]): fully_preserved - appearance.\ndetailed_description: [Shot 1] A man turns.\noverall_soundscape: Quiet.\nnon_diegetic_music: N/A"
    ctx=Context(mode='ref2va', n_pictures=0,n_videos=1,duration_s=15,declared_roles=(("<Video 1>","motion_reference","attribute_transfer"),))
    assert any(f.rule=='R33-motion-cited-as-content' for f in validate(text,ctx))
    assert not any(f.rule=='R33-motion-cited-as-content' for f in validate(text,replace(ctx,declared_roles=(("<Video 1>","edit_source","fully_preserved"),))))


def test_misplaced_existing_cut_time_is_reordered_without_invention():
    text="detailed_description:\n[Shot 1] Static Shot. A waits.\n[Shot 2] Static Shot. At 00:06.000, B nods.\noverall_soundscape: Quiet.\nnon_diegetic_music: N/A\n"
    r=repair(text,target=Target.build(15),mode=Mode.REF2VA,labels=())
    assert '[Shot 2] At 00:06.000, Static Shot. B nods.' in r.text
    assert re.findall(r'At \d{2}:\d{2}\.\d{3}',r.text)==['At 00:06.000']
    assert not any(f.rule=='T4-missing-cut-time' for f in validate(r.text,Context(mode='ref2va',n_pictures=0,duration_s=15)))
    missing=text.replace('At 00:06.000, ','')
    assert 'At ' not in repair(missing,target=Target.build(15),mode=Mode.REF2VA,labels=()).text

def test_motion_definition_is_wiring_not_echoed_meta_instructions():
    text="subject_definitions:\n<Subject 1> is the man in <Picture 1>.\n<Video 1> is a SUBJECT-MOTION REFERENCE. Give <Video 1> a line. Do not define a subject.\nsummary: [reference generation] The man turns.\nretention_analysis:\n<Video 1>: weak_reference - action.\ndetailed_description: [Shot 1] Static Shot. The man turns.\noverall_soundscape: Quiet.\nnon_diegetic_music: N/A\n"
    r=repair(text,target=Target.build(15),mode=Mode.REF2VA,labels=('<Picture 1>','<Video 1>'),motion_labels=('<Video 1>',))
    assert 'Give <Video 1>' not in r.text and 'subject-motion reference' in r.text
    assert r.text.split('summary:')[1]==text.split('summary:')[1]
    untouched=repair(text,target=Target.build(15),mode=Mode.REF2VA,labels=('<Picture 1>','<Video 1>'))
    assert 'Give <Video 1>' in untouched.text
