import React, { useId } from 'react';
import { AbsoluteFill, useCurrentFrame, useVideoConfig } from 'remotion';

export type Highlight = {
  /** Normalized coordinates in the original media, before object-fit. */
  rect: [number, number, number, number];
  /** Seconds relative to this shot, independent of media trim and speed. */
  start: number;
  end: number;
  label?: string;
  style?: 'outline' | 'spotlight';
};
export type Box = { x: number; y: number; width: number; height: number };
const clamp = (value: number, min: number, max: number) => Math.min(max, Math.max(min, value));
const smooth = (value: number) => { const t = clamp(value, 0, 1); return t * t * (3 - 2 * t); };

/** Match the centered CSS object-fit geometry, including letterboxing and crop. */
export function fittedMediaBox(sourceWidth: number, sourceHeight: number, width: number, height: number, fit = 'contain'): Box | null {
  if (![sourceWidth, sourceHeight, width, height].every(n => Number.isFinite(n) && n > 0)) return null;
  const scale = fit === 'cover' ? Math.max(width / sourceWidth, height / sourceHeight) : Math.min(width / sourceWidth, height / sourceHeight);
  const displayedWidth = sourceWidth * scale, displayedHeight = sourceHeight * scale;
  return { x: (width - displayedWidth) / 2, y: (height - displayedHeight) / 2, width: displayedWidth, height: displayedHeight };
}

export function highlightBox(rect: Highlight['rect'], media: Box, width: number, height: number): Box | null {
  if (!Array.isArray(rect) || rect.length !== 4 || !rect.every(Number.isFinite)) return null;
  const [x, y, w, h] = rect;
  if (x < 0 || y < 0 || w <= 0 || h <= 0 || x + w > 1.000001 || y + h > 1.000001) return null;
  const left = clamp(media.x + x * media.width, 0, width), top = clamp(media.y + y * media.height, 0, height);
  const right = clamp(media.x + (x + w) * media.width, 0, width), bottom = clamp(media.y + (y + h) * media.height, 0, height);
  return right > left && bottom > top ? { x: left, y: top, width: right - left, height: bottom - top } : null;
}

export function highlightMotion(frame: number, fps: number, start: number, end: number, reducedMotion = false) {
  if (![frame, fps, start, end].every(Number.isFinite) || fps <= 0 || start < 0 || end <= start) return { opacity: 0, offset: 0 };
  const time = frame / fps;
  if (time < start || time >= end) return { opacity: 0, offset: 0 };
  if (reducedMotion) return { opacity: 1, offset: 0 };
  const fade = Math.min(.18, (end - start) / 3);
  const enter = smooth((time - start) / fade), leave = smooth((end - time) / fade);
  return { opacity: enter * leave, offset: 5 * (1 - enter) };
}

export function highlightLabel(value: string | undefined): string {
  const chars = Array.from((value ?? '').replace(/\s+/g, ' ').trim());
  return chars.length <= 24 ? chars.join('') : `${chars.slice(0, 23).join('')}…`;
}

const overlaps = (a: Box, b: Box, gap = 0) => a.x < b.x + b.width + gap && a.x + a.width > b.x - gap && a.y < b.y + b.height + gap && a.y + a.height > b.y - gap;

/** Keep labels outside all active targets and the bottom subtitle area. If no
 * clear position exists, omit the label instead of obscuring a demonstrated UI. */
export function placeHighlightLabel(target: Box, label: string, width: number, height: number, occupied: Box[] = []): Box | null {
  if (!label) return null;
  const margin = 24, gap = 16, fontSize = 26, labelHeight = 48, safeBottom = height * .80;
  const units = Array.from(label).reduce((sum, char) => sum + (/[^\x00-\x7f]/.test(char) ? 1 : .65), 0);
  const labelWidth = Math.min(width - margin * 2, 660, Math.ceil(units * fontSize + 32));
  if (labelWidth <= 0) return null;
  const x = clamp(target.x, margin, width - margin - labelWidth);
  const nearY = clamp(target.y, margin, safeBottom - labelHeight);
  const candidates = [
    { x, y: target.y - gap - labelHeight },
    { x: target.x + target.width + gap, y: nearY },
    { x: target.x - gap - labelWidth, y: nearY },
    { x, y: target.y + target.height + gap },
  ];
  for (const point of candidates) {
    const box = { ...point, width: labelWidth, height: labelHeight };
    if (box.x < margin || box.y < margin || box.x + box.width > width - margin || box.y + box.height > safeBottom) continue;
    if ([target, ...occupied].some(other => overlaps(box, other, 8))) continue;
    return box;
  }
  return null;
}

type Props = {
  highlights?: Highlight[];
  sourceWidth?: number;
  sourceHeight?: number;
  width: number;
  height: number;
  fit?: string;
  accent?: string;
  foreground?: string;
  surface?: string;
  reducedMotion?: boolean;
};

export const Highlights: React.FC<Props> = ({ highlights = [], sourceWidth = 0, sourceHeight = 0, width, height, fit = 'contain', accent = '#e7a878', foreground = '#f2e9df', surface = '#282220', reducedMotion = false }) => {
  const frame = useCurrentFrame(), { fps } = useVideoConfig(), maskId = useId();
  const media = fittedMediaBox(sourceWidth, sourceHeight, width, height, fit);
  if (!media || !highlights.length) return null;
  const active = highlights.flatMap((highlight, index) => {
    const motion = highlightMotion(frame, fps, highlight.start, highlight.end, reducedMotion);
    const box = highlightBox(highlight.rect, media, width, height);
    return box && motion.opacity > 0 ? [{ ...highlight, ...motion, box, index, label: highlightLabel(highlight.label) }] : [];
  });
  const occupied = active.map(item => item.box);
  const labels = active.map(item => {
    const box = placeHighlightLabel(item.box, item.label, width, height, occupied);
    if (box) occupied.push(box);
    return box;
  });
  const spotlights = active.filter(item => item.style === 'spotlight');
  return <AbsoluteFill style={{ pointerEvents: 'none', overflow: 'hidden' }}>
    {spotlights.length > 0 && <svg width={width} height={height} style={{ position: 'absolute', inset: 0 }}>
      <defs><mask id={maskId} maskUnits="userSpaceOnUse" x={0} y={0} width={width} height={height}>
        <rect width={width} height={height} fill="white" />
        {active.map(item => <rect key={item.index} x={item.box.x} y={item.box.y} width={item.box.width} height={item.box.height} rx={8} fill="black" />)}
      </mask></defs>
      <rect width={width} height={height * .80} fill="#000" opacity={.35 * Math.max(...spotlights.map(item => item.opacity))} mask={`url(#${maskId})`} />
    </svg>}
    {active.map((item, i) => <React.Fragment key={item.index}>
      <div style={{ position: 'absolute', left: item.box.x, top: item.box.y, width: item.box.width, height: item.box.height,
        boxSizing: 'border-box', border: `3px solid ${accent}`, borderRadius: 8, opacity: item.opacity,
        boxShadow: '0 0 0 1px rgba(0,0,0,.35)' }} />
      {labels[i] && <div style={{ position: 'absolute', left: labels[i]!.x, top: labels[i]!.y, width: labels[i]!.width, height: labels[i]!.height,
        boxSizing: 'border-box', padding: '7px 15px', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap',
        fontFamily: 'ProductVideoFont, sans-serif', fontSize: 26, lineHeight: '32px', fontWeight: 600,
        color: foreground, background: surface, border: `1px solid ${accent}`, borderRadius: 8,
        opacity: item.opacity, transform: `translateY(${-item.offset}px)` }}>{item.label}</div>}
    </React.Fragment>)}
  </AbsoluteFill>;
};
