import test from 'node:test';
import assert from 'node:assert/strict';
import {toFinal, layoutPieces} from '../src/lib/time.ts';
import {buildPages} from '../src/lib/captions.ts';
import {scaleAt, pieceSpans} from '../src/lib/zoom.ts';

const pieces = [
  {seg: 0, src_s: 10, src_e: 14, rate: 1.2, scale: 1.0},
  {seg: 1, src_s: 20, src_e: 24, rate: 1.2, scale: 1.1},
  {seg: 1, src_s: 24, src_e: 25, rate: 1.5, scale: 1.1},
];

test('toFinal divides by rate piecewise', () => {
  assert.ok(Math.abs(toFinal(2.4, pieces) - 2.0) < 1e-9);
  assert.ok(Math.abs(toFinal(8, pieces) - 8 / 1.2) < 1e-9);
  assert.ok(Math.abs(toFinal(9, pieces) - (8 / 1.2 + 1 / 1.5)) < 1e-9);
});

test('layout is frame exact and contiguous', () => {
  const {frames, total} = layoutPieces(pieces, 30);
  let acc = 0;
  for (const f of frames) {
    assert.equal(f.startFrame, acc);
    acc += f.durFrames;
  }
  assert.equal(acc, total);
  assert.ok(Math.abs(total - Math.round((8 / 1.2 + 1 / 1.5) * 30)) <= 1);
});

const words = [
  {w: 'Полезный', s: 0.0, e: 0.5}, {w: 'видео', s: 0.5, e: 0.9}, {w: 'делает', s: 0.9, e: 1.3}, {w: 'тебя', s: 1.3, e: 1.6},
  {w: 'глупее.', s: 1.6, e: 2.2}, {w: 'Смотришь,', s: 2.4, e: 3.0}, {w: 'лайкаешь', s: 3.0, e: 3.5, emph: true},
];

test('captions: no ghost word, no overlap, all words kept', () => {
  const pages = buildPages(words, pieces, {maxWords: 3, maxChars: 16});
  assert.equal(pages.flatMap((p) => p.words).length, words.length);
  for (let i = 0; i < pages.length; i++) {
    const last = pages[i].words[pages[i].words.length - 1];
    assert.ok(pages[i].tOut <= last.e + 0.12 + 0.25 + 1e-9, 'visible far past its last word');
    if (pages[i + 1]) assert.ok(pages[i].tOut <= pages[i + 1].tIn + 1e-9, 'overlap');
    assert.ok(pages[i].words.length <= 3);
  }
});

test('captions: last page ends right after the last word (defect c regression)', () => {
  const pages = buildPages(words, pieces, {maxWords: 3, maxChars: 16});
  const lastW = words[words.length - 1];
  const fEnd = toFinal(lastW.e, pieces);
  assert.ok(pages[pages.length - 1].tOut <= fEnd + 0.13 + 1e-9);
});

test('zoom never exceeds 1.25 and snaps in', () => {
  const spans = pieceSpans(pieces);
  const z = [{t: 1, dur: 0.6, peak: 1.22}];
  const f0 = toFinal(1, pieces);
  assert.ok(Math.abs(scaleAt(f0 - 0.01, pieces, z, spans) - 1.0) < 1e-9);
  assert.ok(scaleAt(f0 + 0.12, pieces, z, spans) > 1.2);
  for (let t = 0; t < 8; t += 0.05) assert.ok(scaleAt(t, pieces, z, spans) <= 1.25);
});
