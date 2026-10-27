// Video-only render of one montage plan with Remotion. Concurrency 1. Audio is built separately (tools/audio_mix.py).
// Usage: node tools/render.mjs <plan.json> <rawVideo> <out.mp4> [--still <frame> <png>] [--stills f1,f2,.. <png prefix>] [--range a-b]
// Designed to run unchanged in CI: inputs are local files (CI fetches them from signed URLs first); logs carry only the task id.
import {bundle} from '@remotion/bundler';
import {renderMedia, renderStill, selectComposition, ensureBrowser} from '@remotion/renderer';
import fs from 'node:fs';
import path from 'node:path';
import {fileURLToPath} from 'node:url';

const here = path.dirname(fileURLToPath(import.meta.url));
const root = path.resolve(here, '..');
const [planPath, rawPath, outPath, ...rest] = process.argv.slice(2);
const plan = JSON.parse(fs.readFileSync(planPath, 'utf8'));
const tag = `task-${plan.id}`;
const log = (...a) => console.log(`[${tag}]`, ...a);

// per-run public dir: fonts + hard link of the raw footage (no copy of 44 MB)
const pub = path.join(root, 'work', plan.id, 'public');
fs.mkdirSync(path.join(pub, 'fonts'), {recursive: true});
for (const f of ['Montserrat-Black.ttf', 'Nunito-VF.ttf']) fs.copyFileSync(path.join(root, 'public', 'fonts', f), path.join(pub, 'fonts', f));
const rawLink = path.join(pub, 'raw.mp4');
if (!fs.existsSync(rawLink)) {
  try { fs.linkSync(path.resolve(rawPath), rawLink); } catch { fs.copyFileSync(path.resolve(rawPath), rawLink); }
}

await ensureBrowser();
const serveUrl = await bundle({entryPoint: path.join(root, 'src', 'index.ts'), publicDir: pub});
const inputProps = {plan, videoSrc: 'raw.mp4'};
const comp = await selectComposition({serveUrl, id: 'Montage', inputProps});
log('composition', comp.durationInFrames, 'frames', comp.fps, 'fps');

const common = {
  composition: comp,
  serveUrl,
  inputProps,
  chromiumOptions: {gl: 'angle'},
  offthreadVideoCacheSizeInBytes: 256 * 1024 * 1024,
  timeoutInMilliseconds: 180000,
};

const ssi = rest.indexOf('--stills');
const si = rest.indexOf('--still');
if (ssi >= 0) {
  for (const f of rest[ssi + 1].split(',').map(Number)) await renderStill({...common, frame: f, output: `${rest[ssi + 2]}_f${f}.png`});
  log('stills done');
} else if (si >= 0) {
  await renderStill({...common, frame: Number(rest[si + 1]), output: rest[si + 2]});
  log('still done');
} else {
  const ri = rest.indexOf('--range');
  const frameRange = ri >= 0 ? rest[ri + 1].split('-').map(Number) : null;
  let last = -1;
  await renderMedia({
    ...common,
    codec: 'h264',
    crf: 19,
    pixelFormat: 'yuv420p',
    concurrency: 1,
    muted: true,
    frameRange,
    outputLocation: outPath,
    onProgress: ({progress}) => {
      const p = Math.floor(progress * 10);
      if (p !== last) { last = p; log('progress', p * 10, '%'); }
    },
  });
  log('render done');
}
