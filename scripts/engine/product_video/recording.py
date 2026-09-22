"""Record the existing observed web-capture plan into a screen-video asset."""
from pathlib import Path
import tempfile
import time

from .capture import capture_web, read_plan, web_locator, web_session
from .common import VideoError, file_record, probe, run, write_json


def validate_recording(spec, base):
    from .config import known, number, text
    known(spec, ('source', 'trim_start', 'speed'), 'recording')
    text(spec.get('source'), 'recording.source', 4096)
    source = (base / Path(spec['source']).expanduser()).resolve()
    if not source.is_file() or source.suffix.lower() not in ('.mp4', '.mov', '.webm', '.mkv'):
        raise VideoError('recording.source 需要已有的 MP4、MOV、WebM 或 MKV 视频。')
    number(spec.get('trim_start', 0), 0, 36000, 'recording.trim_start')
    number(spec.get('speed', 1), .1, 4, 'recording.speed')
    spec['source'] = str(source)
    recording_info(spec)


def recording_info(spec, duration=None, fps=30):
    """Validate the real source interval; never stretch, loop or freeze to fill."""
    import math
    data = probe(spec['source'])
    video = next((s for s in data['streams'] if s['codec_type'] == 'video'), None)
    if not video:
        raise VideoError('recording 素材没有视频轨。')
    try:
        length = float(video.get('duration', data['format']['duration']))
        if not math.isfinite(length) or length <= 0:
            raise ValueError()
    except (KeyError, ValueError, TypeError):
        raise VideoError('无法读取 recording 素材的有效时长。') from None
    trim = spec.get('trim_start', 0)
    if trim >= length:
        raise VideoError('recording.trim_start 已超过素材末尾。')
    # Account for rounding the source in-point to the composition frame grid.
    start = round(trim * fps) / fps
    if duration is not None and start + duration * spec.get('speed', 1) > length + 1/fps:
        raise VideoError('recording 素材长度不足以覆盖本镜头；请缩短镜头、调整裁入或提供完整录屏，不会自动循环。')
    return {'width': video['width'], 'height': video['height'], 'duration': length}


def record_web(plan_path, destination):
    plan = read_plan(plan_path)
    if plan['target']['provider'] != 'web':
        raise VideoError('record-web 需要 web 采集计划；原生应用录屏请提供已录制的视频素材。')
    if plan['shots'][0]['actions']:
        raise VideoError('录屏计划的第一项应为无 actions 的 ready 初始画面，后续项目再操作。')
    if any(s['mask'] for s in plan['shots']):
        raise VideoError('record-web 不支持截图 mask。请使用已去除私人内容的演示页面，或先准备处理后的录屏。')
    destination = Path(destination).expanduser().resolve()
    if destination.suffix.lower() != '.mp4':
        raise VideoError('record-web 输出路径使用 .mp4。')
    if destination.exists():
        raise VideoError('录屏输出已存在，请使用新文件名以保留已有素材。')
    destination.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='product-video-record-', dir=destination.parent) as temporary:
        folder = Path(temporary)
        events = []
        with web_session(plan['target'], record_dir=folder) as page:
            web_locator(page, plan['shots'][0]['ready']).wait_for(state='visible')
            source = Path(page.video.path())
            start = time.monotonic()
            page.wait_for_timeout(650)
            for shot in plan['shots']:
                events.append({'shot': shot['id'], 'start': round(time.monotonic() - start, 3)})
                capture_web(page, plan['target'], shot, folder / (shot['id'] + '.png'), recording=True)
            page.wait_for_timeout(900)
            elapsed = time.monotonic() - start
        total = float(probe(source)['format']['duration'])
        offset = max(0, total - elapsed)
        pending = folder / 'screen.mp4'
        run(['ffmpeg', '-v', 'error', '-y', '-i', source, '-ss', str(offset), '-t', str(elapsed),
             '-an', '-c:v', 'libx264', '-preset', 'fast', '-crf', '16', '-pix_fmt', 'yuv420p',
             '-movflags', '+faststart', pending], timeout=max(120, int(elapsed * 3)))
        run(['ffmpeg', '-v', 'error', '-xerror', '-i', pending, '-f', 'null', '-'])
        duration = float(probe(pending)['format']['duration'])
        write_json(destination.with_suffix('.recording.json'), {
            'provider': 'playwright-video', 'plan_file': file_record(plan_path),
            'file': file_record(pending), 'duration': duration, 'events': events,
            'timing': 'Wall-clock shot starts; inspect frames before using precise action cuts.',
            'full_decode': 'passed', 'visual_review': 'unverified'})
        pending.replace(destination)
    print(f'网页录屏已保存并通过完整解码：{destination}', flush=True)
    return destination
