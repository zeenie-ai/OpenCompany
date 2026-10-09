/**
 * Settings > Plugins (design handoff "Settings: Billing, Skills, Plugins"),
 * on the shared catalog page: starter bundles, each a job with the apps it
 * needs and the skills that help with it (hire/starters.json).
 *
 * Hire puts the bundle's skills in the library (switching on any that are
 * off), then hires the bundle's starter as it stands, with no setup screen,
 * and shows the new employee (genui's useStarterHire). A bundle can be
 * hired again, so Discover offers Hire on every card. Yours lists the
 * installed bundles, those with all their skills in the library; nothing
 * else is stored, and removing a skill is done on Skills.
 */

import { useMemo, useState } from 'react';
import { useStarterHire } from '../genui';
import { useSkillLibrary } from '../data/skills';
import { STARTERS, type Starter } from '../hire/templates';
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

  const hireStarter = async (item: CatalogItem) => {
    const starter = STARTERS.find((candidate) => candidate.id === item.id);
    if (!starter || hiring) return;
    if (busy) {
      pillToast('Another hire is still going through. Try again in a moment.', { tone: 'info' });
      return;
    }
    setHiring(starter.id);
    spikeOrb(SPIKE.pluginInstall);
    const hired = await hire(starter);
    setHiring(null);
    // The new employee's page is already showing behind Settings.
    if (hired) useHomeStore.getState().closeSettings();
  };

  return (
    <CatalogLayout
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
      onAdd={(item) => void hireStarter(item)}
    />
  );
}

export default PluginsTab;
