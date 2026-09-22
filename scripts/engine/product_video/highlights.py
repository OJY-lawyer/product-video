"""Timed annotations in normalized source coordinates, sharing screenshot transforms."""
from PIL import Image, ImageColor, ImageDraw

from .common import VideoError
from .motion import smooth


def validate_highlights(items):
    from .config import known, number, text
    if not isinstance(items, list) or len(items) > 8:
        raise VideoError('highlights 需为最多八项的标注数组。')
    for mark in items:
        known(mark, ('rect', 'start', 'end', 'label', 'style'), 'highlights')
        rect = mark.get('rect')
        if not isinstance(rect, list) or len(rect) != 4:
            raise VideoError('highlights.rect 需要 [x,y,width,height] 四个素材比例。')
        for value in rect:
            number(value, 0, 1, 'highlights.rect')
        x, y, w, h = rect
        if w <= 0 or h <= 0 or x+w > 1+1e-9 or y+h > 1+1e-9:
            raise VideoError('highlights.rect 必须为素材范围内的非零矩形。')
        number(mark.get('start'), 0, 3600, 'highlights.start')
        number(mark.get('end'), mark['start'], 3600, 'highlights.end')
        if mark['end'] <= mark['start']:
            raise VideoError('highlights.end 必须晚于 start。')
        if mark.get('style', 'outline') not in ('outline', 'spotlight'):
            raise VideoError('highlights.style 仅支持 outline 或 spotlight。')
        if 'label' in mark:
            text(mark['label'], 'highlights.label', 24)


def validate_step_duration(step, duration, fps):
    if any(mark['end'] > duration + 1/fps for mark in step.get('highlights', [])):
        raise VideoError('highlights.end 超过本镜头时长；start/end 使用镜头内秒数。')
    if 'recording' in step:
        from .recording import recording_info
        recording_info(step['recording'], duration, fps)


def projected_rect(rect, view, clip):
    """Return the visible target after the exact camera/contain transform."""
    x, y, w, h = rect
    vx, vy, vw, vh = view
    cx, cy, cw, ch = clip
    left, top = max(cx, vx+x*vw), max(cy, vy+y*vh)
    right, bottom = min(cx+cw, vx+(x+w)*vw), min(cy+ch, vy+(y+h)*vh)
    return (left, top, right, bottom) if right > left and bottom > top else None


def paint_highlights(image, items, elapsed, view, clip, video, font, reveal=0):
    scale = image.width / 1920
    accent = ImageColor.getrgb(video['accent'])[:3]
    surface = ImageColor.getrgb(video['surface'])[:3]
    foreground = ImageColor.getrgb(video['foreground'])[:3]
    visible = []
    for mark in items:
        start = max(mark['start'], reveal)
        if not start <= elapsed < mark['end']:
            continue
        area = projected_rect(mark['rect'], view, clip)
        if not area:
            continue
        fade = min(.18, (mark['end']-start)/3)
        opacity = 1 if video['reduced_motion'] else min(smooth((elapsed-start)/fade), smooth((mark['end']-elapsed)/fade))
        visible.append((mark, area, opacity))
    spotlights = [(area, opacity) for mark, area, opacity in visible if mark.get('style') == 'spotlight']
    if spotlights:
        shade = Image.new('RGBA', image.size)
        d = ImageDraw.Draw(shade)
        cx, cy, cw, ch = clip
        d.rectangle((cx, cy, cx+cw, cy+ch), fill=(0, 0, 0, round(115*max(opacity for _, opacity in spotlights))))
        for area, _ in spotlights:
            d.rectangle(area, fill=(0, 0, 0, 0))
        image.paste(shade, (0, 0), shade)
    occupied = [area for _, area, _ in visible]
    for mark, area, opacity in visible:
        overlay = Image.new('RGBA', image.size)
        draw = ImageDraw.Draw(overlay)
        left, top, right, bottom = area
        draw.rounded_rectangle(area, radius=max(1, round(7*scale)),
            outline=(*accent, round(255*opacity)), width=max(1, round(3*scale)))
        if mark.get('label'):
            pad, gap = 10*scale, 7*scale
            box = draw.textbbox((0, 0), mark['label'], font=font)
            width, height = box[2]-box[0]+2*pad, box[3]-box[1]+2*pad
            cx, cy, cw, ch = clip
            label_x = min(max(cx, left), cx+cw-width)
            candidates = [(label_x, top-height-gap), (label_x, bottom+gap),
                          (right+gap, top), (left-width-gap, top)]
            for lx, ly in candidates:
                candidate = (lx, ly, lx+width, ly+height)
                if lx < cx or ly < cy or lx+width > cx+cw or ly+height > min(cy+ch, image.height*.8):
                    continue
                if any(lx < b[2]+gap and lx+width > b[0]-gap and ly < b[3]+gap and ly+height > b[1]-gap for b in occupied):
                    continue
                draw.rounded_rectangle(candidate, radius=max(1, round(6*scale)),
                    fill=(*surface, round(245*opacity)), outline=(*accent, round(255*opacity)), width=max(1, round(scale)))
                draw.text((lx+pad-box[0], ly+pad-box[1]), mark['label'], font=font,
                    fill=(*foreground, round(255*opacity)))
                occupied.append(candidate)
                break  # Preserve the outline when no safe label position exists.
        image.paste(overlay, (0, 0), overlay)
