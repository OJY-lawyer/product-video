"""Specific, advisory pacing notes; never rewrite authored timing or call TTS."""
import re
from pathlib import Path

from .common import VideoError, write_json
from .motion import interaction_for


DEFAULT_LIMITS = {'reading_units_per_second': 6, 'caption_seconds': 1.2,
                  'result_hold_seconds': 1.2, 'static_seconds': 8}


def reading_units(text):
    return sum(1 if len(token) == 1 else max(1, len(token)/4)
               for token in re.findall(r'[\u3400-\u9fff]|[A-Za-z0-9]+', text))


def assess(config, chapters, limits=None):
    limits = DEFAULT_LIMITS | (limits or {})
    notes = []
    def note(chapter, code, start, end, reason, suggestion, step=None):
        notes.append({'chapter': chapter['id'], 'title': chapter['title'], 'code': code,
                      'start': round(start, 3), 'end': round(end, 3), 'step': step,
                      'reason': reason, 'suggestion': suggestion})
    silent = config['voice'].get('mode') == 'none'
    for chapter in chapters:
        if silent:
            for cue in chapter['cues']:
                duration = cue['end']-cue['start']
                rate = reading_units(cue['text']) / max(.001, duration)
                if rate > limits['reading_units_per_second'] or duration < limits['caption_seconds']:
                    note(chapter, 'caption-reading', cue['start'], cue['end'],
                         f'字幕显示 {duration:.2f} 秒，约 {rate:.1f} 阅读单位/秒：{cue["text"]}',
                         '延长该字幕与章节、拆成两句，或在动作后单独留出阅读时间；明确时长不会自动改变。')
        sources = set()
        has_continuous = False
        for index, step in enumerate(chapter['steps']):
            end_at = chapter['steps'][index+1]['at'] if index+1 < len(chapter['steps']) else 1
            duration = (end_at-step['at'])*chapter['audio_duration']
            start = chapter['start']+chapter['lead']+step['at']*chapter['audio_duration']
            sources.update(step.get('images', []))
            has_continuous |= 'recording' in step or 'scene3d' in step
            previous = chapter['steps'][index-1] if index else None
            action = step.get('click') or step.get('interaction', {}).get('kind') in ('click', 'drag')
            transition = min(step.get('transition_duration', config['video']['transition']), duration*.3)
            if step.get('transition') == 'cut':
                transition = 0
            try:
                track = interaction_for(step, previous, config['video'], duration, transition)
            except VideoError as exc:
                # The renderer owns timing validity. Keep this report advisory.
                note(chapter, 'action-budget', start, start+duration, str(exc),
                     '增加本步骤的时长，或缩短明确指定的指针移动、停留与等待。', index)
                track = None
            if action and track:
                hold = duration-track['reveal']-transition
                if hold < limits['result_hold_seconds']:
                    note(chapter, 'result-hold', start+track['reveal'], start+duration,
                         f'操作结果稳定展示仅约 {max(0,hold):.2f} 秒。',
                         '把下一步稍后开始，或增加本章时长，让观众先看清结果再切走。', index)
                if previous and previous.get('images') == step.get('images'):
                    note(chapter, 'unchanged-result', start, start+duration,
                         '点击前后引用同一张截图，无法展示可见状态变化。',
                         '补充已核实的操作后截图；仅指向目标时改用 kind: move。', index)
                for mark in step.get('highlights', []):
                    if mark['end'] <= track['reveal']:
                        note(chapter, 'hidden-highlight', start+mark['start'], start+mark['end'],
                             '这项结果标注在结果出现前已结束，因此不会显示。',
                             '将标注开始、结束时间移到结果出现以后。', index)
            if step.get('images') and not track and not step.get('highlights') and duration > limits['static_seconds']:
                note(chapter, 'long-static', start, start+duration,
                     f'同一截图持续 {duration:.1f} 秒，未安排重点标注或状态变化。',
                     '若需阅读可保留；若讲操作，请按语义拆为整体、目标、动作与结果，避免只延长轮播。', index)
        if len(sources) == 1 and not has_continuous:
            note(chapter, 'single-state', chapter['start'], chapter['end'],
                 '本章只有一个截图状态，尚不能呈现完整操作过程。',
                 '总览或静态说明可以保留；功能教程请补操作前状态、关键动作与可见结果，或使用真实录屏。')
    return {'status': 'advisory', 'timing_changed': False, 'duration': chapters[-1]['end'],
            'limits': limits, 'reading_unit_note': '中文逐字、英文约四字母为一阅读单位；仅作节奏提示，仍需观看成片。',
            'notes': notes}


def write_review(config, chapters, folder):
    report = assess(config, chapters)
    target = Path(folder) / 'pacing-review.json'
    write_json(target, report)
    print(f'节奏提示 {len(report["notes"])} 项（不修改时长）：{target}', flush=True)
    return report
