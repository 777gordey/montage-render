// One conversion point: cut timeline (seconds) -> final timeline (seconds). Mirrors tools/montage_plan.py:to_final.
export type Face = {x: number; y: number; h: number};
export type Piece = {seg: number; src_s: number; src_e: number; rate: number; scale: number; face?: Face};

export function toFinal(tCut: number, pieces: Piece[]): number {
  let accCut = 0;
  let accFin = 0;
  for (const p of pieces) {
    const d = p.src_e - p.src_s;
    if (tCut <= accCut + d + 1e-9) return accFin + (tCut - accCut) / p.rate;
    accCut += d;
    accFin += d / p.rate;
  }
  return accFin;
}

export type PieceFrames = Piece & {startFrame: number; durFrames: number; srcStartFrame: number};

// Frame-exact layout: boundaries rounded cumulatively, so durations always sum to total.
export function layoutPieces(pieces: Piece[], fps: number): {frames: PieceFrames[]; total: number} {
  let accFin = 0;
  const frames: PieceFrames[] = [];
  for (const p of pieces) {
    const start = Math.round(accFin * fps);
    accFin += (p.src_e - p.src_s) / p.rate;
    const end = Math.round(accFin * fps);
    frames.push({...p, startFrame: start, durFrames: Math.max(1, end - start), srcStartFrame: Math.round(p.src_s * fps)});
  }
  return {frames, total: frames.length ? frames[frames.length - 1].startFrame + frames[frames.length - 1].durFrames : 0};
}
