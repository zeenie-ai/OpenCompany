/**
 * OpenCompany logo: the OC mark and the wordmark.
 *
 * The mark is an O (the orb's ring: a purple-to-cyan gradient stroke with a
 * core dot) linked to a C (a cyan-to-green arc) with two employee nodes at the
 * C's ends. It is inline SVG that reads the `--lg-*` palette (themes/light.css,
 * themes/dark.css), so it switches with the theme family. Geometry and motion
 * come from design_handoff_opencompany_home (README "Logo",
 * reference/OpenCompany Logo.dc.html).
 *
 * Standalone assets (favicon, desktop icon) are the handoff's signed SVG files
 * in client/public, copied byte for byte; this component re-draws the mark,
 * it never embeds those files.
 */

import { useEffect, useId, useLayoutEffect, useRef, type Ref } from 'react';
import { animate } from '@/lib/motion';
import { claimLogoIntro } from './logoIntro';
import { cn } from '@/lib/utils';

/** Mark heights from the handoff. Below 16px use the app icon instead. */
const SIZES = {
  sidebar: { mark: 23, word: 'text-md', gap: 'gap-2.25' },
  header: { mark: 20, word: 'text-lead', gap: 'gap-2' },
  settings: { mark: 16, word: 'text-sm', gap: 'gap-2' },
} as const;

export type LogoSize = keyof typeof SIZES;

const VIEWBOX_W = 140;
const VIEWBOX_H = 96;

/** Intro and pulse timings from the handoff's logo spec (ms). */
const MOTION = {
  ring: 780,
  cDraw: 760,
  cDrawDelay: 220,
  nodePop: 520,
  nodeDelays: [820, 930],
  wordmark: 520,
  wordmarkDelay: 300,
  pulseNode: 560,
  pulseStagger: 90,
  pulseRing: 900,
} as const;

interface MarkShapesProps {
  uid: string;
  ringRef?: Ref<SVGCircleElement>;
  arcRef?: Ref<SVGPathElement>;
  topNodeRef?: Ref<SVGCircleElement>;
  bottomNodeRef?: Ref<SVGCircleElement>;
}

/** The mark's gradients and shapes, shared by the logo and the static mark. */
function MarkShapes({ uid, ringRef, arcRef, topNodeRef, bottomNodeRef }: MarkShapesProps) {
  const ringGradient = `oc-o-${uid}`;
  const arcGradient = `oc-c-${uid}`;
  return (
    <>
      <defs>
        <linearGradient id={ringGradient} x1="0" y1="0" x2="1" y2="1">
          <stop offset="0" className="[stop-color:var(--lg-ring-a)]" />
          <stop offset="1" className="[stop-color:var(--lg-ring-b)]" />
        </linearGradient>
        <linearGradient id={arcGradient} x1="0" y1="1" x2="1" y2="0">
          <stop offset="0" className="[stop-color:var(--lg-ring-b)]" />
          <stop offset="1" className="[stop-color:var(--lg-green)]" />
        </linearGradient>
      </defs>
      {/* Paint order matters: the C arc sits under the ring where they meet. */}
      <path
        ref={arcRef}
        d="M 113.8 28.2 A 28 28 0 1 0 113.8 67.8"
        fill="none"
        stroke={`url(#${arcGradient})`}
        strokeWidth={14}
        strokeLinecap="round"
        pathLength={1}
        strokeDasharray="1"
      />
      <circle
        ref={ringRef}
        cx="44"
        cy="48"
        r="28"
        fill="none"
        stroke={`url(#${ringGradient})`}
        strokeWidth={14}
        className="origin-center [transform-box:fill-box]"
      />
      <circle cx="44" cy="48" r="8" className="fill-lg-core" />
      <circle
        ref={topNodeRef}
        cx="113.8"
        cy="28.2"
        r="9"
        className="origin-center fill-lg-pink [transform-box:fill-box]"
      />
      <circle
        ref={bottomNodeRef}
        cx="113.8"
        cy="67.8"
        r="9"
        className="origin-center fill-lg-yellow [transform-box:fill-box]"
      />
    </>
  );
}

/** useId output is not guaranteed to be a valid url(#...) fragment. */
function useGradientUid(): string {
  return useId().replace(/[^a-zA-Z0-9_-]/g, '');
}

/** The mark alone, filling the width of its box and never animated: the
 *  orb's stand-in where WebGL or motion is unavailable. Decorative. */
export function OcMark({ className }: { className?: string }) {
  const uid = useGradientUid();
  return (
    <svg
      viewBox={`0 0 ${VIEWBOX_W} ${VIEWBOX_H}`}
      className={cn('block h-auto overflow-visible', className)}
      aria-hidden
      focusable="false"
    >
      <MarkShapes uid={uid} />
    </svg>
  );
}

export interface OcLogoProps {
  size?: LogoSize;
  /** Draw the wordmark next to the mark. When hidden, the mark itself is labelled. */
  wordmark?: boolean;
  /** Play the intro animation (once per page load). */
  intro?: boolean;
  /** Change this number to play the pulse (e.g. after a hire). 0 = never. */
  pulseNonce?: number;
  className?: string;
}

export function OcLogo({ size = 'sidebar', wordmark = true, intro = false, pulseNonce = 0, className }: OcLogoProps) {
  const spec = SIZES[size];
  const height = spec.mark;
  const width = Math.round((height * VIEWBOX_W) / VIEWBOX_H);

  const uid = useGradientUid();

  const ringRef = useRef<SVGCircleElement>(null);
  const arcRef = useRef<SVGPathElement>(null);
  const topNodeRef = useRef<SVGCircleElement>(null);
  const bottomNodeRef = useRef<SVGCircleElement>(null);
  const wordRef = useRef<HTMLSpanElement>(null);

  useLayoutEffect(() => {
    if (!intro || !claimLogoIntro()) return;
    animate(
      ringRef.current,
      [
        { transform: 'scale(0.2) rotate(-180deg)', opacity: 0 },
        { transform: 'none', opacity: 1 },
      ],
      { duration: MOTION.ring, easing: 'overshoot' },
    );
    animate(arcRef.current, [{ strokeDashoffset: 1 }, { strokeDashoffset: 0 }], {
      duration: MOTION.cDraw,
      delay: MOTION.cDrawDelay,
      easing: 'reveal',
    });
    [topNodeRef.current, bottomNodeRef.current].forEach((node, i) => {
      animate(node, [{ transform: 'scale(0)' }, { transform: 'scale(1)' }], {
        duration: MOTION.nodePop,
        delay: MOTION.nodeDelays[i],
        easing: 'overshoot',
      });
    });
    animate(
      wordRef.current,
      [
        { opacity: 0, transform: 'translateX(-6px)' },
        { opacity: 1, transform: 'none' },
      ],
      { duration: MOTION.wordmark, delay: MOTION.wordmarkDelay, easing: 'spring' },
    );
  }, [intro]);

  useEffect(() => {
    if (!pulseNonce) return;
    [topNodeRef.current, bottomNodeRef.current].forEach((node, i) => {
      animate(
        node,
        [{ transform: 'scale(1)' }, { transform: 'scale(1.45)', offset: 0.4 }, { transform: 'scale(1)' }],
        { duration: MOTION.pulseNode, delay: MOTION.pulseStagger * i, easing: 'spring', fill: 'none' },
      );
    });
    animate(
      ringRef.current,
      [
        { transform: 'rotate(0deg) scale(1)' },
        { transform: 'rotate(180deg) scale(1.08)', offset: 0.5 },
        { transform: 'rotate(360deg) scale(1)' },
      ],
      { duration: MOTION.pulseRing, easing: 'spring', fill: 'none' },
    );
  }, [pulseNonce]);

  const labelled = !wordmark;

  return (
    <span className={cn('inline-flex shrink-0 items-center', spec.gap, className)}>
      <svg
        viewBox={`0 0 ${VIEWBOX_W} ${VIEWBOX_H}`}
        width={width}
        height={height}
        className="shrink-0 overflow-visible"
        role={labelled ? 'img' : undefined}
        aria-label={labelled ? 'OpenCompany' : undefined}
        aria-hidden={labelled ? undefined : true}
        focusable="false"
      >
        <MarkShapes
          uid={uid}
          ringRef={ringRef}
          arcRef={arcRef}
          topNodeRef={topNodeRef}
          bottomNodeRef={bottomNodeRef}
        />
      </svg>
      {wordmark && (
        <span ref={wordRef} className={cn('font-body tracking-wordmark whitespace-nowrap', spec.word)}>
          <span className="font-semibold text-fg-default">Open</span>
          <span className="font-medium text-fg-muted">Company</span>
        </span>
      )}
    </span>
  );
}

export default OcLogo;
