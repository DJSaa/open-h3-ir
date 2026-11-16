# SPDX-License-Identifier: Apache-2.0
import json
from types import SimpleNamespace

import pytest

from h3ir.engine import CompilationCancelled, execution_hooks
from h3ir.intent_resolution import KEY, resolve
from h3ir.models import AssetKind, AssetRef, Brief, DialogueLine, Mode, Role


class Model:
    def __init__(self, rows=None, error=None, content=None):
        self.rows=rows or [];self.error=error;self.content=content;self.calls=[]
    def chat(self,messages,**kwargs):
        self.calls.append((messages,kwargs))
        if self.error:raise self.error
        return SimpleNamespace(content=self.content if self.content is not None else json.dumps({'decisions':self.rows}))


def row(index,value,evidence='',field='role'):
    return {'index':index,'field':field,'value':value,'evidence':evidence}


def brief(prompt='Animate naturally.',kind='fl2va',roles=(None,),audios=(),policy='absent'):
    cfg={'version':1,'workflow':kind,'adaptive_canvas':kind=='fl2va',
         'anchor_canvases':{'0':[1344,768],'1':[768,1344]}}
    assets=[]
    for index,role in enumerate(roles):
        assets.append(AssetRef(AssetKind.IMAGE,Role(role or 'subject'),str(index)*64,
            provenance={'gateway_index':index,KEY:cfg},role_stated=role is not None))
    for n,audio_role in enumerate(audios,len(assets)):
        assets.append(AssetRef(AssetKind.AUDIO,Role(audio_role or 'bgm'),str(n)*64,
            provenance={'gateway_index':n,KEY:cfg},role_stated=audio_role is not None))
    if policy!='absent':
        i=len(assets)
        assets.extend([AssetRef(AssetKind.AUDIO,Role.BGM,'c'*64,paired_video_sha256='d'*64,
            soundtrack_policy=policy,role_stated=True,provenance={'gateway_index':i,'video_number':1,KEY:cfg}),
            AssetRef(AssetKind.VIDEO,Role.EDIT_SOURCE,'d'*64,provenance={'gateway_index':i,KEY:cfg})])
    return Brief(prompt,assets=assets)


@pytest.mark.parametrize('value,mode',[('frame_anchor_first',Mode.I2VA),('frame_anchor_last',Mode.L2VA)])
def test_single_anchor_classification(value,mode):
    b=brief('以图1结束。');m=Model([row(0,value,'以图1结束')])
    result,record=resolve(b,m,seed=7)
    assert result.mode==mode and result.assets[0].role.value==value
    assert record['calls']==1 and record['status']=='resolved'
    messages,kw=m.calls[0]
    assert kw==dict(thinking=False,temperature=0,max_tokens=512,seed=7,retries=0,timeout_s=15)
    assert all(isinstance(item['content'],str) for item in messages)


def test_reverse_two_images_and_literal_dialogue_preserved():
    b=brief('从图2开始，最后定格在图1。角色说：“图1是我的家。”',roles=(None,None))
    b.dialogue=[DialogueLine('图1是我的家。','Chinese','图1')]
    m=Model([row(0,'frame_anchor_last','最后定格在图1'),row(1,'frame_anchor_first','从图2开始')])
    result,record=resolve(b,m)
    assert result.mode==Mode.FL2VA and result.canvas==(768,1344)
    assert result.assets[0].role is Role.FRAME_ANCHOR_LAST
    assert '从图1开始，最后定格在图2' in result.intent
    assert '“图1是我的家。”' in result.intent and result.dialogue[0].text=='图1是我的家。'
    assert result.dialogue[0].speaker_hint=='图2'


@pytest.mark.parametrize('specified,expected',[(0,('frame_anchor_last','frame_anchor_first')),(1,('frame_anchor_last','frame_anchor_first'))])
def test_two_frames_one_resolution_fills_complement(specified,expected):
    text='图2是首帧。图1是尾帧。'
    value='frame_anchor_last' if specified==0 else 'frame_anchor_first'
    result,record=resolve(brief(text,roles=(None,None)),Model([row(specified,value,'图1是尾帧' if specified==0 else '图2是首帧')]))
    assert tuple(a.role.value for a in result.assets)==expected
    assert any(r['source']=='constraint' for r in record['decisions'])
    assert not record['warnings']


@pytest.mark.parametrize('failure',[None,TimeoutError(),ValueError()])
def test_defaults_do_not_abort_compilation(failure):
    b=brief(kind='ref2va',roles=(None,),audios=(None,),policy=None)
    result,record=resolve(b,Model(error=failure))
    assert result.assets[1].role is Role.VOICE_TIMBRE
    assert result.assets[2].soundtrack_policy=='preserve'
    assert record['status']=='defaulted'


def test_audio_and_soundtrack_combined_one_call():
    b=brief('独立音频1用作背景音乐，视频1不要原声。',kind='ref2va',audios=(None,),policy=None)
    result,record=resolve(b,Model([row(1,'bgm','独立音频1用作背景音乐'),row(2,'ignore','视频1不要原声',field='soundtrack_policy')]))
    assert result.assets[1].role is Role.BGM and result.assets[2].soundtrack_policy=='ignore'
    assert record['calls']==1


@pytest.mark.parametrize('role',['frame_anchor_first','frame_anchor_last'])
def test_explicit_parameters_are_locked_and_call_skipped(role):
    b=brief('图片是尾帧。' if role=='frame_anchor_first' else '图片是首帧。',roles=(role,));m=Model()
    result,record=resolve(b,m)
    assert not m.calls and record['status']=='skipped'
    assert result.assets[0].role.value == role


@pytest.mark.parametrize('rows,content',[
    ([row(0,'frame_anchor_last','not in prompt')],None),
    ([row(99,'frame_anchor_last','尾帧')],None),
    ([row(0,'frame_anchor_last','尾帧'),row(0,'frame_anchor_first','尾帧')],None),
    ([row(0,'nonsense','尾帧')],None),
    ([], '{broken'),
    ([], '[]'),
])
def test_untrusted_classification_falls_back(rows,content):
    result,record=resolve(brief('尾帧'),Model(rows,content=content))
    assert result.assets[0].role is Role.FRAME_ANCHOR_FIRST
    assert record['status']=='defaulted' and record['warnings']


def test_duplicate_anchor_decisions_fallback_consistently():
    b=brief('图1首帧，图2首帧。',roles=(None,None))
    result,record=resolve(b,Model([row(0,'frame_anchor_first','图1首帧'),row(1,'frame_anchor_first','图2首帧')]))
    assert [a.role for a in result.assets]==[Role.FRAME_ANCHOR_FIRST,Role.FRAME_ANCHOR_LAST]
    assert record['warnings']


def test_no_opt_in_has_no_new_model_calls():
    b=Brief('unchanged');m=Model()
    result,record=resolve(b,m)
    assert result is b and record is None and not m.calls


def test_cancellation_never_becomes_default():
    with pytest.raises(CompilationCancelled):
        resolve(brief(),Model(error=CompilationCancelled('cancelled')))


def test_no_llm_uses_defaults():
    m=Model();result,record=resolve(brief(roles=(None,None)),m,llm=False)
    assert not m.calls and record['calls']==0
    assert [a.role for a in result.assets]==[Role.FRAME_ANCHOR_FIRST,Role.FRAME_ANCHOR_LAST]


def test_frozen_stage_reused_without_model_call():
    class Hooks:
        value=None
        def check_cancelled(self):pass
        def stage_read(self,name,key):return self.value if self.value and self.value['input_sha256']==key else None
        def stage_write(self,name,key,value):self.value=value
    hooks=Hooks();m=Model([row(0,'frame_anchor_last','尾帧')]);b=brief('尾帧')
    with execution_hooks(hooks):
        one,r1=resolve(b,m);two,r2=resolve(b,m)
    assert len(m.calls)==1 and r1==r2 and one.mode==two.mode==Mode.L2VA
