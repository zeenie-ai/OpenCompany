/**
 * The hire view (design handoff "Hire view"): the orb, a greeting, the
 * question, the composer, starter jobs (each can also be hired as it
 * stands), and the new employee's setup screen once the owner has
 * described one.
 *
 * The hero rises in on arrival (every `[data-intro]` element, staggered).
 */

import { useEffect, useLayoutEffect, useRef, useState } from 'react';
import { stagger } from '@/lib/motion';
import { useConnectors } from '../data/connectors';
import { useEmployeesQuery } from '../data/employees';
import { useAnsweringCount } from '../data/liveTask';
import { callName, useOwnerSettings } from '../data/profile';
import { HireDraftPanel, useHireComposer, useStarterHire } from '../genui';
import { ENERGY, setEnergyTarget } from '../orb/orb';
import { OrbSlot } from '../orb/OrbSlot';
import { useHomeStore } from '../state/homeStore';
import { Composer } from './Composer';
import { greetingFor } from './greeting';
import { TemplateChips } from './TemplateChips';
import { HIRE_TEMPLATES } from './templates';

export function HireView({ onConnect }: { onConnect: (providerId: string) => void }) {
  const composer = useHireComposer();
  const starterHire = useStarterHire();
  const { connectedApps } = useConnectors();
  const { data: employees } = useEmployeesQuery();
  const { data: settings } = useOwnerSettings();
  const focusNonce = useHomeStore((s) => s.composerFocus);
  const consumeComposerFocus = useHomeStore((s) => s.consumeComposerFocus);
  const openSettings = useHomeStore((s) => s.openSettings);
  const [focused, setFocused] = useState(false);
  const rootRef = useRef<HTMLDivElement>(null);

  useLayoutEffect(() => {
    const root = rootRef.current;
    if (!root) return;
    stagger(
      root.querySelectorAll('[data-intro]'),
      [
        { opacity: 0, transform: 'translateY(16px)', filter: 'blur(5px)' },
        { opacity: 1, transform: 'none', filter: 'blur(0)' },
      ],
      { base: 120, step: 80, duration: 'intro', easing: 'spring' },
    );
  }, []);

  // The orb livens up with the composer (design handoff "Orb reactivity").
  const hasText = composer.value.trim().length > 0;
  useEffect(() => {
    setEnergyTarget(composer.working ? ENERGY.generating : hasText ? ENERGY.typing : focused ? ENERGY.focus : ENERGY.idle);
  }, [composer.working, hasText, focused]);
  useEffect(() => () => setEnergyTarget(ENERGY.idle), []);

  const working = useAnsweringCount(employees);
  const name = callName(settings);
  const apps = connectedApps.map((provider) => ({ id: provider.id, name: provider.name, icon_ref: provider.icon_ref }));
  const picked = HIRE_TEMPLATES.find((template) => template.job === composer.value.trim()) ?? null;

  return (
    <div ref={rootRef} className="flex w-full flex-col items-center">
      <div className="mb-6 flex flex-col items-center gap-2.5 text-center">
        <OrbSlot size="hire" />
        <p
          data-intro
          className="m-0 flex items-center gap-2 font-mono text-xs font-medium tracking-label text-fg-muted uppercase"
        >
          <span aria-hidden className="size-1.5 rounded-full bg-current text-status-working-dot shadow-pip" />
          {greetingFor(new Date())}
          {name ? `, ${name}` : ''} · {working} working now
        </p>
        <h1
          data-intro
          className="m-0 max-w-160 text-hero leading-hero font-semibold tracking-hero text-balance text-fg-default"
        >
          Who should we hire today?
        </h1>
        <p data-intro className="m-0 max-w-130 text-lead text-pretty text-fg-muted">
          Describe the job in plain words. They’ll ask you before anything important.
        </p>
      </div>

      <Composer
        value={composer.value}
        onChange={composer.onChange}
        onSubmit={composer.onSubmit}
        refining={composer.refining}
        onStopRefining={composer.onStopRefining}
        working={composer.working}
        apps={apps}
        onOpenApps={() => openSettings('connectors')}
        focusNonce={focusNonce}
        onFocusTaken={consumeComposerFocus}
        onFocusChange={setFocused}
        maxLength={2000}
      />
      <TemplateChips
        picked={picked}
        onPick={(template) => composer.pick(template.job)}
        onHireNow={(template) => void starterHire.hire(template)}
        disabled={composer.working}
        className="mt-4"
      />
      <HireDraftPanel onConnect={onConnect} />
    </div>
  );
}

export default HireView;
