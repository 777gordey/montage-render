import {toFinal, type Piece} from './time.ts';

export type Word = {w: string; s: number; e: number; emph?: boolean};
export type FWord = {text: string; s: number; e: number; emph: boolean};
export type Page = {words: FWord[]; tIn: number; tOut: number};

const endsSentence = (t: string) => /[.!?…]$/.test(t);
const endsClause = (t: string) => /[,;:—]$/.test(t);

// Word timings -> final timeline, grouped into pages of 1..maxWords words.
// Invariant (ghost-word guard, defect c): a page is never visible after its own last word ended
// (plus a short hold) and never overlaps the next page.
export function buildPages(words: Word[], pieces: Piece[], opt: {maxWords: number; maxChars: number; hold?: number; minShow?: number}): Page[] {
  const hold = opt.hold ?? 0.12;
  const minShow = opt.minShow ?? 0.25;
  const fw: FWord[] = words.map((x) => ({text: x.w.replace(/[.,;:…]+$/, '').replace(/^[«"(]+|[»")]+$/g, ''), s: toFinal(x.s, pieces), e: toFinal(x.e, pieces), emph: !!x.emph}));
  const raw = words.map((x) => x.w);
  const pages: FWord[][] = [];
  let cur: FWord[] = [];
  let chars = 0;
  fw.forEach((w, i) => {
    const gap = cur.length ? w.s - cur[cur.length - 1].e : 0;
    if (cur.length && (cur.length >= opt.maxWords || chars + 1 + w.text.length > opt.maxChars || gap > 0.4)) {
      pages.push(cur);
      cur = [];
      chars = 0;
    }
    cur.push(w);
    chars += (chars ? 1 : 0) + w.text.length;
    const last = i === fw.length - 1;
    if (last || endsSentence(raw[i]) || (endsClause(raw[i]) && cur.length >= 2)) {
      pages.push(cur);
      cur = [];
      chars = 0;
    }
  });
  if (cur.length) pages.push(cur);
  const out: Page[] = pages.map((ws) => ({words: ws, tIn: ws[0].s, tOut: ws[ws.length - 1].e + hold}));
  for (let i = 0; i < out.length; i++) {
    const next = out[i + 1];
    if (out[i].tOut - out[i].tIn < minShow) out[i].tOut = out[i].tIn + minShow; // extend, never drop words
    if (next && out[i].tOut > next.tIn) out[i].tOut = next.tIn; // no overlap, no ghost
    if (next && next.tIn - out[i].tOut < 0.0) out[i].tOut = next.tIn;
  }
  return out;
}

export function activeWord(page: Page, t: number): number {
  let idx = -1;
  page.words.forEach((w, i) => {
    if (t >= w.s - 1e-6) idx = i;
  });
  return idx;
}
