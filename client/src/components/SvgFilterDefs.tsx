/**
 * Mounts a hidden inline <svg><defs> block once at the app root so per-
 * theme CSS rules like `filter: url(#ink-blot)` resolve. The SVG itself
 * has zero pixel footprint (width=0 height=0); the filter definitions
 * inside <defs> are document-wide referenceable by ID.
 *
 * Filter IDs declared:
 *   #ink-blot - Renaissance edge warble (turbulence + displacement map)
 *   #noise    - Wasteland paper-grain turbulence
 *   #crt      - Cyber chromatic aberration / scanline
 *
 * Audit: only `#ink-blot` is currently referenced in the upstream design
 * handoff CSS (renaissance.css line 271). `#noise` and `#crt` are shipped
 * preemptively so per-theme CSS can adopt them without a second wave.
 */
import React from 'react';

export const SvgFilterDefs: React.FC = () => (
  <svg
    width={0}
    height={0}
    aria-hidden="true"
    style={{ position: 'absolute', overflow: 'hidden' }}
  >
    <defs>
      <filter id="ink-blot">
        <feTurbulence
          type="fractalNoise"
          baseFrequency="0.02 0.04"
          numOctaves="2"
          seed="3"
        />
        <feDisplacementMap in="SourceGraphic" scale="2" />
      </filter>
      <filter id="noise">
        <feTurbulence
          type="fractalNoise"
          baseFrequency="0.9"
          numOctaves="2"
          stitchTiles="stitch"
        />
        <feColorMatrix values="0 0 0 0 0  0 0 0 0 0  0 0 0 0 0  0 0 0 0.15 0" />
      </filter>
      <filter id="crt">
        <feColorMatrix values="1 0 0 0 0  0 1 0 0 0  0 0 1 0 0  0 0 0 1 0" />
        <feOffset in="SourceGraphic" dx="-1" dy="0" result="r" />
        <feOffset in="SourceGraphic" dx="1" dy="0" result="b" />
        <feBlend in="r" in2="b" mode="screen" />
      </filter>
    </defs>
  </svg>
);

export default SvgFilterDefs;
