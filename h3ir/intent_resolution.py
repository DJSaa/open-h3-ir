# SPDX-License-Identifier: Apache-2.0
"""Bounded, text-only intent resolution before semantic planning.

Classification never owns filenames, label allocation or render structure. Those
are computed from validated choices. An omitted choice remains distinct from an
explicit input; old briefs without the opt-in provenance retain their contract.
"""
from __future__ import annotations

import hashlib
import json
import re
import time
from dataclasses import replace

from .models import AssetKind, Mode, Role

VERSION = 1
KEY = 'dingo_intent_resolution'
from pathlib import Path

SYSTEM = (Path(__file__).parent / 'prompts' / 'intent_resolution.v1.txt').read_text().strip()


def context(brief):
    values = [(a.provenance or {}).get(KEY) for a in brief.assets]
    found = [v for v in values if v is not None]
    if not found:
        return None
    cfg = found[0]
    if (not isinstance(cfg, dict) or cfg.get('version') != VERSION
            or cfg.get('workflow') not in ('fl2va', 'ref2va')
            or any(v != cfg for v in found)):
        raise ValueError('invalid intent resolution context')
    return cfg


def _index(asset):
    value = (asset.provenance or {}).get('gateway_index')
    if type(value) is not int or value < 0:
        raise ValueError('missing material identity')
    return value


def _items(brief, cfg):
    items = []
    image_n = video_n = standalone_n = 0
    for asset in brief.assets:
        idx = _index(asset)
        if asset.kind is AssetKind.IMAGE:
            image_n += 1
            if cfg['workflow'] == 'fl2va':
                items.append({'index':idx,'kind':'image','name':f'图{image_n} / <Picture {image_n}>',
                              'field':'role','explicit':asset.role.value if asset.role_stated else None,
                              'allowed':['frame_anchor_first','frame_anchor_last']})
        elif asset.kind is AssetKind.VIDEO:
            video_n += 1
        elif asset.kind is AssetKind.AUDIO:
            if asset.paired_video_sha256:
                # The provenance carries the original video index, not the audio label.
                video_number = (asset.provenance or {}).get('video_number')
                items.append({'index':idx,'kind':'video_soundtrack','name':f'视频{video_number}的原音轨',
                              'field':'soundtrack_policy','explicit':asset.soundtrack_policy,
                              'allowed':['preserve','ignore']})
            else:
                standalone_n += 1
                # Final Audio numbering includes video soundtracks, unlike standalone ordinals.
                audio_number = sum(a.kind is AssetKind.AUDIO for a in brief.assets[:brief.assets.index(asset)+1])
                items.append({'index':idx,'kind':'audio','name':f'独立音频{standalone_n} / <Audio {audio_number}>',
                              'field':'role','explicit':asset.role.value if asset.role_stated else None,
                              'allowed':['voice_timbre','bgm']})
    return items


def _parse(reply):
    text = reply.content.strip()
    if text.startswith('```'):
        text = re.sub(r'^```(?:json)?\s*|\s*```$', '', text)
    value = json.loads(text)
    if not isinstance(value, dict) or set(value) != {'decisions'} or not isinstance(value['decisions'], list):
        raise ValueError('invalid decision envelope')
    return value['decisions']


def _remap_pictures(brief, mapping):
    """Rename lexical reference tokens; literal speech and quoted text stay intact."""
    if all(a == b for a,b in mapping.items()):
        return brief
    token = re.compile(r'<d>.*?</d>|“[^”]*”|「[^」]*」|『[^』]*』|"[^"\n]*"|<Picture\s+(\d+)>|\bPicture\s+(\d+)\b|图\s*(\d+)(?!\d)', re.S|re.I)
    def convert(m):
        number = next((v for v in m.groups() if v is not None), None)
        if number is None:
            return m.group()
        old = int(number);new = mapping.get(old, old)
        if m.group().startswith('<'):return f'<Picture {new}>'
        if m.group().lower().startswith('picture'):return f'Picture {new}'
        return f'图{new}'
    def text(value):return token.sub(convert, value)
    return replace(brief, intent=text(brief.intent), dialogue=[replace(d,speaker_hint=text(d.speaker_hint)) if d.speaker_hint else d for d in brief.dialogue])


def resolve(brief, backend, *, seed=None, llm=True):
    cfg = context(brief)
    if cfg is None:
        return brief, None
    start = time.monotonic()
    items = _items(brief,cfg)
    pending = [i for i in items if i['explicit'] is None]
    sources = {(i['index'],i['field']):('parameter' if i['explicit'] is not None else 'default') for i in items}
    selected = {(i['index'],i['field']):i['explicit'] for i in items}
    evidence = {};warnings=[];calls=0;failure=None
    source_text = brief.intent+'\n'+'\n'.join(d.text for d in brief.dialogue)
    fingerprint=hashlib.sha256(json.dumps({'text':source_text,'items':items,'cfg':cfg},ensure_ascii=False,sort_keys=True).encode()).hexdigest()
    from .engine import checkpoint, stage_read, stage_write
    checkpoint()
    cached = stage_read('intent_resolution', fingerprint)
    if cached is not None:
        return _apply(brief, cfg, cached), cached
    if pending and llm:
        try:
            calls=1
            reply=backend.chat([{'role':'system','content':SYSTEM}, {'role':'user','content':json.dumps(
                {'prompt':brief.intent,'dialogue':[d.text for d in brief.dialogue],
                 'workflow':cfg['workflow'],'materials':items,'pending':pending},ensure_ascii=False)}],
                thinking=False,temperature=0,max_tokens=512,seed=seed,retries=0,timeout_s=15)
            decisions=_parse(reply)
            indexed={}
            for row in decisions:
                if (not isinstance(row,dict) or set(row)!={'index','field','value','evidence'}
                        or type(row['index']) is not int or not isinstance(row['field'],str)):
                    raise ValueError('invalid decision row')
                key=(row['index'],row['field'])
                if key in indexed:raise ValueError('duplicate material decision')
                indexed[key]=row
            permitted={(i['index'],i['field']) for i in pending}
            if set(indexed)-permitted:raise ValueError('decision for a locked or unknown material')
            for item in pending:
                key=(item['index'],item['field']);row=indexed.get(key)
                if not row or row['value']=='unknown':
                    warnings.append({'code':'intent_resolution_defaulted','index':item['index']})
                    continue
                quote=row['evidence']
                if (row['value'] not in item['allowed'] or not isinstance(quote,str)
                        or not quote.strip() or len(quote)>300 or quote not in source_text):
                    warnings.append({'code':'intent_resolution_defaulted','index':item['index']})
                    continue
                selected[key]=row['value'];sources[key]='prompt';evidence[key]=quote
        except Exception as exc:
            from .engine import CompilationCancelled
            if isinstance(exc,CompilationCancelled):raise
            failure=type(exc).__name__
            warnings.append({'code':'intent_resolution_defaulted'})
            for item in pending:
                key=(item['index'],item['field']);selected[key]=None;sources[key]='default';evidence.pop(key,None)
    anchors=[i for i in items if i['kind']=='image']
    anchor_values=[selected[(i['index'],'role')] for i in anchors]
    if len(anchors)==2:
        if anchor_values[0] is not None and anchor_values[1] is not None and anchor_values[0]==anchor_values[1]:
            for i in anchors:
                key=(i['index'],'role')
                if i['explicit'] is None:selected[key]=None;sources[key]='default';evidence.pop(key,None)
            warnings.append({'code':'intent_resolution_defaulted'})
        for item in anchors:
            key=(item['index'],'role')
            if selected[key] is None:
                other=next(i for i in anchors if i is not item)
                other_value=selected[(other['index'],'role')]
                if other_value:
                    selected[key]='frame_anchor_last' if other_value=='frame_anchor_first' else 'frame_anchor_first'
                    sources[key]='constraint'
                else:
                    selected[key]='frame_anchor_first'
    for item in items:
        key=(item['index'],item['field'])
        if selected[key] is None:
            selected[key]=('frame_anchor_first' if item['kind']=='image' else 'voice_timbre' if item['kind']=='audio' else 'preserve')
    warnings = [w for w in warnings if ('index' not in w or any(
        sources[(i['index'],i['field'])] == 'default' for i in items if i['index'] == w['index']))]
    result={'version':VERSION,'status':('defaulted' if failure or any(sources[(i['index'],i['field'])]=='default' for i in pending) else 'resolved') if pending else 'skipped',
            'calls':calls,'seconds':round(time.monotonic()-start,6),'failure_type':failure,
            'decisions':[{'index':i['index'],'field':i['field'],'value':selected[(i['index'],i['field'])],
                          'source':sources[(i['index'],i['field'])],'evidence':evidence.get((i['index'],i['field']))} for i in items],
            'warnings':warnings,'input_sha256':fingerprint}
    resolved=_apply(brief,cfg,result)
    stage_write('intent_resolution',fingerprint,result)
    checkpoint()
    return resolved,result


def _apply(brief,cfg,record):
    values={(i['index'],i['field']):i['value'] for i in record['decisions']}
    assets=[]
    for asset in brief.assets:
        idx=_index(asset)
        if asset.kind is AssetKind.AUDIO and asset.paired_video_sha256:
            asset=replace(asset,soundtrack_policy=values[(idx,'soundtrack_policy')])
        elif (idx,'role') in values:
            asset=replace(asset,role=Role(values[(idx,'role')]),role_stated=True)
        assets.append(asset)
    brief=replace(brief,assets=assets)
    if cfg['workflow']=='fl2va':
        images=[a for a in assets if a.kind is AssetKind.IMAGE]
        if images:
            order=sorted(images,key=lambda a:a.role is Role.FRAME_ANCHOR_LAST)
            mapping={i+1:order.index(a)+1 for i,a in enumerate(images)}
            brief=_remap_pictures(brief,mapping)
            role=images[0].role
            mode=Mode.FL2VA if len(images)==2 else Mode.L2VA if role is Role.FRAME_ANCHOR_LAST else Mode.I2VA
            brief=replace(brief,mode=mode)
            canvases=cfg.get('anchor_canvases',{})
            if cfg.get('adaptive_canvas'):
                canvas=canvases.get(str(_index(order[0])))
                if (not isinstance(canvas,list) or len(canvas)!=2 or any(type(v) is not int or v<=0 or v%32 for v in canvas)):
                    raise ValueError('missing adaptive canvas for resolved anchor')
                brief=replace(brief,canvas=tuple(canvas),aspect=f'{canvas[0]}:{canvas[1]}',megapixels=canvas[0]*canvas[1]/1_000_000)
    return brief
