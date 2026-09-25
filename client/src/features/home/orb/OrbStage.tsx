/**
 * The orb's canvas host (design handoff orb/ORB.md): fills Home's main area
 * below the header, behind the content, and never takes pointer events.
 */

import { useLayoutEffect, useRef } from 'react';
import { mountOrb, unmountOrb } from './orb';

export function OrbStage() {
  const ref = useRef<HTMLDivElement>(null);
  useLayoutEffect(() => {
    const element = ref.current;
    if (!element) return;
    mountOrb(element);
    return () => unmountOrb(element);
  }, []);
  return (
    <div
      ref={ref}
      aria-hidden
      className="pointer-events-none absolute inset-x-0 top-(--h-home-header) bottom-0 z-0 overflow-hidden"
    />
  );
}

export default OrbStage;
