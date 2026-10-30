"""Chinese deterministic floor. Protocol tokens remain byte-exact; prose is Chinese.

The floor refers to measured assets rather than translating or inventing their identity
attributes. The native-language writer may elaborate those facts; failed enrichment returns
this auditable, deliberately thin floor, never an English substitute.
"""
import re
from dataclasses import replace
from .models import Mode
from .grid import ms_to_timestamp
from .render import RenderResult, instruction_line
from .language import protected_intent, restore_dialogue, HAN, CAMERA_ZH
from .textnorm import normalize


def render_chinese(plan, brief, opts, *, enriched=False):
    sections = {}; bodies = {}; shots = []
    speakers = [replace(s, subject=None) for s in plan.speakers] if plan.mode.is_base else plan.speakers
    for shot in plan.shots:
        local_dialogue = enriched and bool(HAN.search(shot.body))
        render_brief = replace(brief, dialogue=shot.dialogue) if local_dialogue else brief
        if local_dialogue:
            body = shot.body
        else:
            body = '依据用户要求呈现画面与动作：' + normalize(protected_intent(brief))
            if plan.mode is Mode.FL2VA:
                body += '。从Picture 1的画面出发，连续过渡至Picture 2所示的终态。'
            elif plan.mode is Mode.L2VA:
                body += '。连续过渡至Picture 1所示的终态。'
            else:
                for m in plan.manifest:
                    body += f' 采用{m.label}作为对应参考。'
            if plan.mode is Mode.REF2VA:
                body += ' ' + '、'.join(s.label for s in plan.subjects) + '保持各自参考外观。'
        body = body.replace('{{CAM}}', '镜头保持用户指定的运动方式。')
        camera_type = 'Static Shot' if any(x in brief.intent for x in ['固定镜头','固定机位','镜头别动','镜头不动']) else (shot.camera.type if shot.camera else 'Static Shot')
        body += ' '+CAMERA_ZH[camera_type]+'。'
        # Structured speech is inserted once, at the planned shot. A model cannot supply it.
        for i, line in enumerate(render_brief.dialogue, 1):
            token = '{{D%d}}' % i
            if line not in shot.dialogue:
                body = body.replace(token, '')
        body = restore_dialogue(body, render_brief, speakers)
        from .render import dialogue_markup
        for line in shot.dialogue:
            if line.text not in body:
                body += ' ' + dialogue_markup(line, next((s for s in speakers if s.descriptor == (line.speaker_hint or "")), None), "Chinese")
        if plan.mode.is_base:
            body = re.sub(r'<Picture (\d+)>', r'Picture \1', body)
        if shot.n == 1:
            for text in brief.onscreen_text:
                if text not in body:body += ' 画面文字："'+text+'"。'
        bodies[shot.n] = body
        lead = '' if shot.n == 1 else f'At {ms_to_timestamp(shot.start_ms)}, 镜头切换到'
        shots.append(f'[Shot {shot.n}] {lead}{body}')
    desc = ' '.join(shots)
    if plan.mode is Mode.REF2VA:
        defs = [f'{s.label} 是来源于' + '、'.join(s.sources) + '的参考主体，外观依据这些参考。' for s in plan.subjects]
        defined = ' '.join(defs)
        for m in plan.manifest:
            if m.label not in defined:
                defs.append(f'{m.label} 是目标视频对应的参考素材。')
        prefix = '[' + ' + '.join(plan.task_types) + '] '
        summary = prefix + '目标视频依据参考素材和用户要求生成。'
        if 'video editing' in plan.task_types:
            clip = next(m.label for m in plan.manifest if m.kind.value == 'video')
            summary = prefix + f'目标视频是{clip}的编辑版本。按用户要求处理画面。'
        ret = []
        scope = ', '.join(f'[Shot {s.n}]' for s in plan.shots)
        for subject in plan.subjects:
            ret.append(f'{subject.label} (appears in {scope}): {subject.retention} - 依据参考和用户指定关系保留。')
        for m in plan.manifest:
            if not any(line.startswith(m.label+' ') for line in ret):
                marker = ('fully_copy' if m.role.value=='bgm' else 'reference') if m.kind.value=='audio' else 'fully_preserved'
                ret.append(f'{m.label}: {marker} - 按对应参考用途使用。')
        sections.update(subject_definitions='\n'.join(defs), summary=summary,
                        retention_analysis='\n'.join(ret), detailed_description='依据用户要求保持画面风格。\n'+desc)
    else:
        sections['integrated_multimodal_description'] = desc
    sections['overall_soundscape'] = '保持用户指定的对白和环境声音。' if brief.dialogue else '仅保留与画面一致的轻微环境声。'
    music_refs=[m.label for m in plan.manifest if m.kind.value=='audio' and m.role.value=='bgm']
    sections['non_diegetic_music'] = ('、'.join(music_refs)+'作为背景音乐，遵循用户指定的原旋律、节奏及混音要求。') if music_refs and not brief.silent else 'N/A'
    sep = opts.field_separator
    prompt = sep.join(f'{k}:\n{v}' if plan.mode is Mode.REF2VA else f'{k}: {v}' for k,v in sections.items())
    instr = instruction_line(plan, opts)
    if instr:prompt = instr+'\n\n'+prompt
    return RenderResult(prompt=prompt.strip()+'\n', sections=sections, notes=[], shot_bodies=bodies)
