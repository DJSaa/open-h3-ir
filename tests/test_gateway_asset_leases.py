"""Pins cover upload-to-compile gaps and overlapping users of the same hash."""
import hashlib
import os
from dataclasses import replace
from types import SimpleNamespace
import pytest
from fastapi import HTTPException
from h3ir import uploads, asset_leases
from h3ir.config import get_config, set_config, AssetConfig, Paths
from h3ir.models import AssetKind, Role, ManifestEntry
from h3ir.plan import audio_relations


@pytest.fixture
def isolated(tmp_path):
    old = get_config()
    set_config(replace(old, paths=Paths(state_dir=tmp_path),
                       assets=AssetConfig(upload_max_bytes=100, upload_store_bytes=150, upload_ttl_hours=1)))
    asset_leases._LEASES.clear()
    yield tmp_path
    asset_leases._LEASES.clear()
    set_config(old)


def put(raw):
    digest=hashlib.sha256(raw).hexdigest()
    with uploads.receiver(digest,declared=len(raw)) as rx:
        rx.write(raw)
        path,_=rx.finish()
    os.utime(path,(1,1))
    return digest,path


def test_lease_prevents_eviction_before_compile_and_between_users(isolated):
    digest,path=put(b'x'*100)
    asset_leases.reserve('one',[digest]);asset_leases.reserve('two',[digest])
    uploads.sweep(now=100000)
    assert path.exists()
    asset_leases.release('one');uploads.sweep(now=100000)
    assert path.exists()
    asset_leases.release('two');uploads.sweep(now=100000)
    assert not path.exists()


def test_executing_lease_never_expires_or_releases(isolated,monkeypatch):
    digest,path=put(b'x'*100)
    asset_leases.reserve('one',[digest])
    with asset_leases.compiling('one',[digest]):
        asset_leases._LEASES['one']['deadline']=0
        uploads.sweep(now=100000)
        assert path.exists()
        with pytest.raises(HTTPException):asset_leases.release('one')
        with pytest.raises(HTTPException):
            with asset_leases.compiling('one',[digest]):pass
    asset_leases.release('one');uploads.sweep(now=100000)
    assert not path.exists()


def test_store_full_does_not_evict_pinned_input(isolated):
    digest,path=put(b'x'*100);asset_leases.reserve('one',[digest])
    with pytest.raises(uploads.StoreFull):
        put(b'y'*100)
    assert path.exists()


def test_paired_audio_ignore_changes_semantics_without_removing_label():
    m=ManifestEntry(slot=0,label='<Audio 1>',kind=AssetKind.AUDIO,sha256='a'*64,
                    wiring='ref_video_audio_1',role=Role.BGM,paired_with='<Video 1>',soundtrack_policy='ignore')
    p=SimpleNamespace(manifest=[m],speakers=[])
    assert audio_relations(p)==[('<Audio 1>','weak_reference','the paired source soundtrack is not retained in the target audio')]
    from h3ir.render import render_subject_definitions
    p.subjects=[]
    assert 'not retained' in render_subject_definitions(p)


def test_brief_cache_is_bounded():
    from h3ir import service
    service._STORE.clear()
    for i in range(140):service._remember(str(i),None,None)
    assert len(service._STORE)<=128
    for rec in service._STORE.values():rec['at']=0
    with service._LOCK:service._prune_briefs()
    assert not service._STORE


def test_analysis_cache_bound_and_atomic_replacement(isolated):
    from h3ir import cache_io
    set_config(replace(get_config(), assets=replace(get_config().assets, analysis_cache_bytes=150)))
    root=isolated/'cache'/'cards';root.mkdir(parents=True)
    cache_io.write(root/'one.json','x'*100)
    cache_io.write(root/'two.json','y'*100)
    assert sum(p.stat().st_size for p in root.glob('*.json'))<=150
    assert cache_io.read(root/'two.json')=='y'*100
    assert not list(root.glob('*.part'))
    cache_io.write(root/'oversize.json','z'*200)
    assert not (root/'oversize.json').exists()


def test_compile_workspace_is_isolated_and_cleaned(isolated):
    digest,path=put(b'x'*10);asset_leases.reserve('one',[digest])
    with asset_leases.compiling('one',[digest]):
        work=asset_leases.analysis_workspace();assert work is not None
        (work/'test-frame.jpg').write_bytes(b'frame')
    assert asset_leases.analysis_workspace() is None
    assert not work.exists()
    assert path.exists()


def test_ignore_policy_reaches_task_types_and_writer_facts():
    from h3ir.models import Brief
    from h3ir.plan import derive_task_types
    from h3ir.prose import audio_task_facts
    ignored=ManifestEntry(slot=0,label='<Audio 1>',kind=AssetKind.AUDIO,sha256='a'*64,
        wiring='ref_video_audio_1',role=Role.BGM,paired_with='<Video 1>',soundtrack_policy='ignore')
    brief=Brief(intent='Ignore original soundtrack')
    types=derive_task_types([ignored],brief)
    assert 'audio reuse' not in types
    assert 'audio reference' in types
    facts=audio_task_facts(('<Audio 1>',),tuple(types),(('<Audio 1>','bgm'),),('<Audio 1>',))
    assert 'explicit ignore policy' in facts and 'weak_reference' in facts
    assert '`fully_copy` says' not in facts
    # A separate BGM remains a copy even when the paired track is ignored.
    bgm=replace(ignored,slot=1,label='<Audio 2>',wiring='ref_audio_1',paired_with=None,soundtrack_policy=None)
    types=derive_task_types([ignored,bgm],brief)
    assert 'audio reuse' in types and 'audio reference' in types
    facts=audio_task_facts(('<Audio 1>','<Audio 2>'),tuple(types),
        (('<Audio 1>','bgm'),('<Audio 2>','bgm')),('<Audio 1>',))
    assert '`fully_copy` says' in facts


def test_explicit_bgm_licenses_music_in_restrained_chinese_request():
    from h3ir.compile import _scope
    from h3ir.models import Brief
    from h3ir.creativity import SCORE
    m=ManifestEntry(slot=0,label='<Audio 1>',kind=AssetKind.AUDIO,sha256='a'*64,
        wiring='ref_audio_1',role=Role.BGM)
    plan=SimpleNamespace(manifest=[m],label_counts=lambda:{'Audio':1})
    brief=Brief(intent='以独立音频作为背景音参考',creativity='restrained')
    assert _scope(brief,plan).permits(SCORE)
    assert not _scope(replace(brief,silent=True),plan).permits(SCORE)
    plan.manifest=[replace(m,soundtrack_policy='ignore')]
    assert not _scope(brief,plan).permits(SCORE)


def test_audio_marker_contract_rejects_copy_reference_swap():
    from h3ir.validate import Context,validate
    ctx=Context(audio_marker_constraints=(('<Audio 1>',('weak_reference',)),
        ('<Audio 2>',('fully_copy','partially_copy'))))
    def errors(markers):
        text='subject_definitions:\n<Audio 1> is ignored.\n<Audio 2> is music.\nsummary:\n[reference generation]\nretention_analysis:\n'+markers+'\ndetailed_description:\n[Shot 1] A car.\noverall_soundscape:\nRoom tone.\nnon_diegetic_music:\n<Audio 2> plays.'
        return [x for x in validate(text,ctx) if x.rule=='R35-audio-marker-contract']
    assert not errors('<Audio 1>: weak_reference - ignored\n<Audio 2>: partially_copy - background audio')
    assert len(errors('<Audio 1>: fully_copy - copied\n<Audio 2>: reference - only style'))==2
