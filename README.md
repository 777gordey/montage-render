# montage-render

Code-only short-form video montage layer ([Remotion](https://www.remotion.dev/) + ffmpeg) that renders one talking-head clip from an edit-plan JSON on a GitHub Actions runner. No media is stored in this repository.

## How a job runs
1. `tools/dispatch_job.py` (your machine) serves the plan and the raw clip from a throw-away local server through an HTTPS quick tunnel, behind HMAC-signed URLs that expire and are revoked when the job ends.
2. It encrypts the three URLs (raw, plan, result upload) into one dispatch input with the Actions secret `MONTAGE_BUNDLE_KEY`, because dispatch inputs of a public repository are readable by anyone.
3. `.github/workflows/render.yml` (`workflow_dispatch`, one job at a time, 40 min timeout) decrypts and masks them, fetches the inputs, downloads the CC-licensed sound packs, renders with `tools/run_clip.py`, and uploads `result.tar` (mp4 + json) with the signed PUT URL.
4. The dispatcher verifies the duration with ffprobe against the plan and records timings.

Logs carry only a generic job id, step timings and error classes. No artifacts, no media cache, no `set -x`.

```
python tools/dispatch_job.py <id> <intake_plan.json> <raw.mp4> --codeword WORD [--emph emph.json] [--expect-dur SEC]
```
Needs: `gh` logged in with `repo` + `workflow` scope, `cloudflared`, `openssl`, `ffprobe`, and `MONTAGE_BUNDLE_KEY` in the environment or `.env.local` (git-ignored; same value as the Actions secret).

## Guards
`git config core.hooksPath .githooks` enables the pre-commit scan (`scripts/guard.py`): refuses staged media files (by extension and by file signature), oversized files, secrets (tokens, keys, signed-URL parameters, tunnel hosts) and personal data (e-mails, phones, public IPs, home paths). `guard.yml` runs the same scan on every tracked file in CI and fails the build.

## Licences
- Remotion has its own licence: free for individuals and for-profit companies with up to 3 employees; check that it applies to you.
- `public/fonts`: Montserrat and Nunito, SIL Open Font License 1.1.
- Sound effects and music beds are downloaded at run time from Mixkit (free licence, not redistributable as a pack) and are never committed.
