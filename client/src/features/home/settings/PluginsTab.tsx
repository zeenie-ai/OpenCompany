/**
 * Settings > Plugins (design handoff "Settings: Billing, Skills, Plugins"),
 * on the shared catalog page: starter bundles, each a job with the apps it
 * needs and the skills that help with it (hire/starters.json).
 *
 * Hire puts the bundle's skills in the library (switching on any that are
 * off), then hires the preset routine with the owner's reviewed reporting lines,
 * and shows the new employee (genui's useStarterHire). A bundle can be
 * hired again, so Discover offers Hire on every card. Yours lists the
 * installed bundles, those with all their skills in the library; nothing
 * else is stored, and removing a skill is done on Skills.
 */

import { useMemo, useState } from 'react';
import { useStarterHire } from '../genui';
import { useSkillLibrary } from '../data/skills';
import { STARTERS, type Starter } from '../hire/templates';
import { StarterTeamReview } from '../hire/StarterTeamReview';
import type { HireTeamMember } from '../genui/hirePayload';
import { useHomeStore } from '../state/homeStore';
import { SPIKE, spikeOrb } from '../orb/orb';
import { pillToast } from '../ui/pillToast';
import type { CatalogItem } from './catalog';
import { CatalogLayout } from './CatalogLayout';

function toItem(starter: Starter, state: CatalogItem['state']): CatalogItem {
  const skills = `${starter.skills.length} skill${starter.skills.length === 1 ? '' : 's'}`;
  return {
    id: starter.id,
    name: starter.label,
    description: starter.summary,
    byline: 'by OpenCompany',
    verified: true,
    meta: [skills, starter.apps.join(' + ')].filter(Boolean).join(' · '),
    tile: { tone: starter.role },
    state,
  };
}

export function PluginsTab() {
  const library = useSkillLibrary();
  const { busy, hire } = useStarterHire();
  const [hiring, setHiring] = useState<string | null>(null);
  const [review, setReview] = useState<Starter | null>(null);

  const discover = useMemo(
    () => STARTERS.map((starter) => toItem(starter, hiring === starter.id ? 'busy' : 'available')),
    [hiring],
  );
  const installed = useMemo(() => {
    const inLibrary = new Set((library.data ?? []).map((row) => row.name));
    return STARTERS.filter((starter) => starter.skills.every((name) => inLibrary.has(name))).map((starter) =>
      toItem(starter, 'added'),
    );
  }, [library.data]);

  const hireStarter = async (starter: Starter, roles: HireTeamMember[]) => {
    if (hiring) return false;
    if (busy) {
      pillToast('Another hire is still going through. Try again in a moment.', { tone: 'info' });
      return false;
    }
    setHiring(starter.id);
    spikeOrb(SPIKE.pluginInstall);
    const hired = await hire(starter, roles);
    setHiring(null);
    // The new employee's page is already showing behind Settings.
    if (hired) useHomeStore.getState().closeSettings();
    return hired;
  };

  return (
    <><CatalogLayout
      title="Plugins"
      searchPlaceholder="Search plugins"
      loading={library.isPending}
      yours={{
        items: installed,
        sectionTitle: 'Installed',
        empty: { title: 'No plugins installed', detail: 'Plugins bundle skills, apps and a routine for a line of work.' },
      }}
      discover={{ items: discover, sectionTitle: 'Top plugins' }}
      verbs={{ add: 'Hire', busy: 'Hiring…' }}
      onAdd={item => {
        if (busy) pillToast('Another hire is still going through. Try again in a moment.', { tone: 'info' });
        else setReview(STARTERS.find(starter => starter.id === item.id) ?? null);
      }}
    />
    {review && <StarterTeamReview key={review.id} starter={review} busy={busy || !!hiring}
      onClose={() => setReview(null)} onHire={roles => hireStarter(review, roles)} />}</>
  );
}

export default PluginsTab;
