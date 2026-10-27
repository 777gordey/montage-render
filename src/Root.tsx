import React from 'react';
import {Composition} from 'remotion';
import {Montage} from './Montage';
import {layoutPieces} from './lib/time.ts';

export const Root: React.FC = () => (
  <Composition
    id="Montage"
    component={Montage as React.FC<any>}
    width={1080}
    height={1920}
    fps={30}
    durationInFrames={300}
    defaultProps={{plan: null, videoSrc: ''}}
    calculateMetadata={({props}: any) => {
      const plan = props.plan;
      if (!plan) return {durationInFrames: 300};
      return {durationInFrames: layoutPieces(plan.pieces, plan.fps).total, fps: plan.fps, width: plan.width, height: plan.height};
    }}
  />
);
