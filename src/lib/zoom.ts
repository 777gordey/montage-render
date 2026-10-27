import {toFinal, type Face, type Piece} from './time.ts';

export type Zoom = {t: number; dur: number; peak: number};

const easeOut = (x: number) => 1 - Math.pow(1 - Math.min(1, Math.max(0, x)), 3);

// Scale at final time t (seconds): per-piece base scale, plus snap-in / ease-out punch zooms.
export type Span = {t0: number; t1: number; scale: number; face?: Face};
export const MAX_SCALE = 1.6;

export function spanAt(t: number, spans: Span[]): Span | undefined {
  return spans.find((f) => t >= f.t0 - 1e-9 && t < f.t1) ?? spans[spans.length - 1];
}

export function scaleAt(t: number, pieces: Piece[], zooms: Zoom[], fpsPieces: Span[]): number {
  let base = 1;
  for (const f of fpsPieces) if (t >= f.t0 - 1e-9 && t < f.t1) base = f.scale;
  let s = base;
  for (const z of zooms) {
    const z0 = toFinal(z.t, pieces);
    const zd = z.dur; // punch durations are final-timeline seconds
    const dt = t - z0;
    if (dt < 0 || dt > zd + 0.3) continue;
    const snap = 0.12; // 80..160 ms snap
    const env = dt < snap ? easeOut(dt / snap) : 1 - easeOut((dt - snap) / Math.max(0.01, zd + 0.3 - snap));
    const peak = Math.max(base, z.peak);
    s = Math.max(s, base + (peak - base) * env);
  }
  return Math.min(MAX_SCALE, s);
}

export function pieceSpans(pieces: Piece[]): Span[] {
  let acc = 0;
  return pieces.map((p) => {
    const t0 = acc;
    acc += (p.src_e - p.src_s) / p.rate;
    return {t0, t1: acc, scale: p.scale, face: p.face};
  });
}
