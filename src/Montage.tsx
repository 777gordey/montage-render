import React, {useMemo} from 'react';
import {AbsoluteFill, OffthreadVideo, Sequence, staticFile, useCurrentFrame, useVideoConfig, interpolate, Easing} from 'remotion';
import {layoutPieces, type Piece} from './lib/time.ts';
import {buildPages, activeWord, type Word} from './lib/captions.ts';
import {scaleAt, pieceSpans, spanAt, type Span, type Zoom} from './lib/zoom.ts';

type Plan = {
  fps: number; width: number; height: number; speed: number; final_duration: number;
  source: {width: number; height: number};
  pieces: Piece[]; words: Word[]; zooms: Zoom[];
  face: {x: number; y: number; h: number};
  hook: {text: string; lines: string[]; key: string; t_out_final: number; font_px?: number};
  cta: {codeword: string; line: string; dur_final: number};
  grade: {brightness: number; contrast: number; saturate: number};
  caption: {font_px: number; max_words: number; max_chars: number; emph_color: string; chin_off: number; min_y: number; max_y: number};
  cutaways: {t: number; dur: number; text: string; style: number}[];
};

const FONT = '"MontserratBlack", "Arial Black", Impact, sans-serif';
const CAP_FONT = '"NunitoVF", "Arial Rounded MT Bold", "Arial Black", sans-serif';
const W = 1080;
const H = 1920;

const Font: React.FC = () => (
  <style>{`@font-face{font-family:"MontserratBlack";src:url("${staticFile('fonts/Montserrat-Black.ttf')}") format("truetype");font-weight:900;}@font-face{font-family:"NunitoVF";src:url("${staticFile('fonts/Nunito-VF.ttf')}") format("truetype");font-weight:200 1000;}`}</style>
);

// Cover-fit geometry: source scaled to fill 1080x1920, centred on the face (clamped to the frame).
const geometry = (plan: Plan) => {
  const sw = plan.source.width;
  const sh = plan.source.height;
  const k = Math.max(W / sw, H / sh);
  const dw = sw * k;
  const dh = sh * k;
  const left = Math.min(0, Math.max(W - dw, W / 2 - plan.face.x * dw));
  const top = Math.min(0, Math.max(H - dh, 0));
  return {dw, dh, left, top};
};

const Footage: React.FC<{plan: Plan; videoSrc: string}> = ({plan, videoSrc}) => {
  const {frames} = useMemo(() => layoutPieces(plan.pieces, plan.fps), [plan]);
  const g = geometry(plan);
  return (
    <>
      {frames.map((f, i) => (
        <Sequence key={i} from={f.startFrame} durationInFrames={f.durFrames} layout="none">
          <OffthreadVideo
            src={videoSrc.startsWith('http') ? videoSrc : staticFile(videoSrc)}
            startFrom={f.srcStartFrame}
            playbackRate={f.rate}
            muted
            style={{position: 'absolute', left: g.left, top: g.top, width: g.dw, height: g.dh, maxWidth: 'none'}}
          />
        </Sequence>
      ))}
    </>
  );
};

// Caption anchor: just under the chin of the CURRENT framing (face box scaled about the face point by the piece base scale),
// so the line follows the face on wide and punch-in shots instead of sitting at a fixed bottom edge.
const captionY = (plan: Plan, span: Span | undefined) => {
  const g = geometry(plan);
  const face = span?.face ?? plan.face;
  const s = span?.scale ?? 1;
  const fy = g.top + face.y * g.dh;
  const chinScaled = fy + (face.h * g.dh) / 2 * s;
  const y = chinScaled + plan.caption.chin_off * H + plan.caption.font_px * 0.6;
  return Math.min(plan.caption.max_y * H, Math.max(plan.caption.min_y * H, y));
};

const Captions: React.FC<{plan: Plan; spans: Span[]}> = ({plan, spans}) => {
  const frame = useCurrentFrame();
  const t = frame / plan.fps;
  const pages = useMemo(
    () => buildPages(plan.words, plan.pieces, {maxWords: plan.caption.max_words, maxChars: plan.caption.max_chars}),
    [plan],
  );
  const hookOn = t < plan.hook.t_out_final; // the hook card carries the opening line
  const cut = plan.cutaways.some((c) => t >= c.t && t < c.t + c.dur); // keyword card on screen: the card is the caption
  const page = hookOn || cut ? undefined : pages.find((p) => t >= p.tIn && t < p.tOut);
  if (!page) return null;
  const a = activeWord(page, t);
  const dt = t - page.tIn;
  const pop = interpolate(dt, [0, 0.13], [0.93, 1], {extrapolateRight: 'clamp', easing: Easing.out(Easing.cubic)});
  const op = interpolate(dt, [0, 0.07], [0, 1], {extrapolateRight: 'clamp'});
  const fs = plan.caption.font_px;
  const y = captionY(plan, spanAt(t, spans));
  return (
    <div style={{position: 'absolute', left: 70, right: 70, top: y, transform: 'translateY(-50%)', display: 'flex', justifyContent: 'center', opacity: op}}>
      <div style={{display: 'flex', flexWrap: 'wrap', justifyContent: 'center', alignItems: 'baseline', columnGap: fs * 0.28, transform: `scale(${pop})`, textAlign: 'center'}}>
        {page.words.map((w, i) => {
          const hot = w.emph && i <= a;
          return (
            <span
              key={i}
              style={{
                fontFamily: CAP_FONT, fontWeight: 900, fontSize: fs, lineHeight: 1.1,
                color: hot ? '#FFFFFF' : plan.caption.emph_color,
                display: 'inline-block', transform: hot ? 'scale(1.14)' : 'scale(1)',
                WebkitTextStroke: `${Math.round(fs * 0.12)}px #1b1203`, paintOrder: 'stroke fill',
                textShadow: '0 3px 10px rgba(0,0,0,.5)',
              }}
            >
              {w.text}
            </span>
          );
        })}
      </div>
    </div>
  );
};

const CARD_STYLES = [
  {bg: '#FFC83D', fg: '#141008'},
  {bg: '#101114', fg: '#FFC83D'},
  {bg: '#F4F1EA', fg: '#141008'},
];

// Full-frame keyword card on a payoff word (cutaway slot). Slot exists to break the single talking-head shot; a b-roll file can replace it later.
const Cutaways: React.FC<{plan: Plan}> = ({plan}) => (
  <>
    {plan.cutaways.map((c, i) => {
      const from = Math.round(c.t * plan.fps);
      const dur = Math.max(1, Math.round(c.dur * plan.fps));
      return (
        <Sequence key={i} from={from} durationInFrames={dur} layout="none">
          <CardFace text={c.text} style={CARD_STYLES[c.style % CARD_STYLES.length]} />
        </Sequence>
      );
    })}
  </>
);

const CardFace: React.FC<{text: string; style: {bg: string; fg: string}}> = ({text, style}) => {
  const frame = useCurrentFrame();
  const k = interpolate(frame, [0, 10], [1.12, 1], {extrapolateRight: 'clamp', easing: Easing.out(Easing.cubic)});
  const fs = Math.max(120, Math.min(300, 960 / (0.62 * text.length)));
  return (
    <AbsoluteFill style={{background: style.bg, justifyContent: 'center', alignItems: 'center'}}>
      <div style={{fontFamily: CAP_FONT, fontWeight: 900, fontSize: fs, color: style.fg, letterSpacing: '-0.02em', transform: `scale(${k})`, textAlign: 'center', padding: '0 50px'}}>{text}</div>
      <div style={{position: 'absolute', left: 90, right: 90, bottom: 640, height: 14, borderRadius: 7, background: style.fg, opacity: 0.9, transform: `scaleX(${interpolate(frame, [0, 12], [0, 1], {extrapolateRight: 'clamp'})})`}} />
    </AbsoluteFill>
  );
};

const HookCard: React.FC<{plan: Plan}> = ({plan}) => {
  const frame = useCurrentFrame();
  const t = frame / plan.fps;
  const out = plan.hook.t_out_final;
  if (t >= out) return null;
  const inS = interpolate(t, [0, 0.18], [0.92, 1], {extrapolateRight: 'clamp', easing: Easing.out(Easing.cubic)});
  const opacity = interpolate(t, [out - 0.2, out], [1, 0], {extrapolateLeft: 'clamp'});
  return (
    <div style={{position: 'absolute', left: 70, right: 70, top: 390, display: 'flex', justifyContent: 'center', opacity, transform: `scale(${inS})`}}>
      <div style={{background: 'rgba(8,8,12,.74)', borderRadius: 36, padding: '34px 44px', textAlign: 'center', boxShadow: '0 12px 40px rgba(0,0,0,.45)', border: `6px solid ${plan.caption.emph_color}`}}>
        {plan.hook.lines.map((ln, i) => (
          <div key={i} style={{fontFamily: FONT, fontWeight: 900, fontSize: plan.hook.font_px ?? 104, lineHeight: 1.1, color: ln.includes(plan.hook.key) ? plan.caption.emph_color : '#fff'}}>
            {ln}
          </div>
        ))}
      </div>
    </div>
  );
};

const EndCard: React.FC<{plan: Plan}> = ({plan}) => {
  const frame = useCurrentFrame();
  const t = frame / plan.fps;
  const t0 = plan.final_duration - plan.cta.dur_final;
  if (t < t0) return null;
  const p = interpolate(t - t0, [0, 0.25], [0, 1], {extrapolateRight: 'clamp', easing: Easing.out(Easing.cubic)});
  return (
    <div style={{position: 'absolute', left: 60, right: 60, top: 560, display: 'flex', justifyContent: 'center', opacity: p, transform: `translateY(${(1 - p) * 40}px)`}}>
      <div style={{background: 'rgba(8,8,12,.8)', borderRadius: 40, padding: '36px 48px', textAlign: 'center', border: `6px solid ${plan.caption.emph_color}`}}>
        <div style={{fontFamily: FONT, fontWeight: 900, fontSize: 60, color: '#fff', lineHeight: 1.1}}>{plan.cta.line.toUpperCase()}</div>
        <div style={{fontFamily: FONT, fontWeight: 900, fontSize: 130, color: plan.caption.emph_color, lineHeight: 1.1, marginTop: 8}}>«{plan.cta.codeword}»</div>
      </div>
    </div>
  );
};

const Progress: React.FC<{plan: Plan}> = ({plan}) => {
  const frame = useCurrentFrame();
  const {durationInFrames} = useVideoConfig();
  return (
    <div style={{position: 'absolute', left: 60, right: 60, top: 270, height: 10, borderRadius: 5, background: 'rgba(255,255,255,.28)'}}>
      <div style={{width: `${(frame / durationInFrames) * 100}%`, height: '100%', borderRadius: 5, background: plan.caption.emph_color}} />
    </div>
  );
};

export const Montage: React.FC<{plan: Plan | null; videoSrc: string}> = ({plan, videoSrc}) => {
  const frame = useCurrentFrame();
  const spans = useMemo(() => (plan ? pieceSpans(plan.pieces) : []), [plan]);
  if (!plan) return <AbsoluteFill style={{background: '#000'}} />;
  const t = frame / plan.fps;
  const s = scaleAt(t, plan.pieces, plan.zooms, spans);
  const g = plan.grade;
  const geo = geometry(plan);
  const face = spanAt(t, spans)?.face ?? plan.face;
  const origin = `${geo.left + face.x * geo.dw}px ${geo.top + face.y * geo.dh}px`; // zoom about the real face point of this shot
  return (
    <AbsoluteFill style={{background: '#000'}}>
      <Font />
      <AbsoluteFill style={{transform: `scale(${s})`, transformOrigin: origin, filter: `brightness(${g.brightness}) contrast(${g.contrast}) saturate(${g.saturate})`}}>
        <Footage plan={plan} videoSrc={videoSrc} />
      </AbsoluteFill>
      <Cutaways plan={plan} />
      <HookCard plan={plan} />
      <Captions plan={plan} spans={spans} />
      <EndCard plan={plan} />
      <Progress plan={plan} />
    </AbsoluteFill>
  );
};
