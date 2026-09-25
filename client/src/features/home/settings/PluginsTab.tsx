/**
 * Settings > Plugins (design handoff "Settings: Billing, Skills, Plugins"),
 * on the shared catalog page: starter bundles, each a job with the apps it
 * needs and the skills that help with it (hire/starters.json).
 *
 * Install adds the bundle's skills to the library (switching on any that
 * are off), then starts a new hire from the bundle's job and shows it on the
 * hire view. A bundle counts as installed when all its skills are in the
 * library, so nothing else is stored; removing a skill is done on Skills.
 */

import { useMemo, useState } from 'react';
import { useStartHire } from '../genui';
import { useDiscoverSkills, useSkillActions, useSkillLibrary } from '../data/skills';
import { STARTERS, type Starter } from '../hire/templates';
import { useHomeStore } from '../state/homeStore';
import { pillToast } from '../ui/pillToast';
import type { CatalogItem } from './catalog';
import { CatalogLayout } from './CatalogLayout';

function meta(starter: Starter): string {
  const skills = `${starter.skills.length} skill${starter.skills.length === 1 ? '' : 's'}`;
  return [skills, starter.apps.join(' + ')].filter(Boolean).join(' · ');
}

export function PluginsTab() {
  const library = useSkillLibrary();
  const discover = useDiscoverSkills();
  const actions = useSkillActions();
  const { busy, start } = useStartHire();
  const [installing, setInstalling] = useState<string | null>(null);

  const rows = useMemo(() => library.data ?? [], [library.data]);
  const items = useMemo<CatalogItem[]>(() => {
    const inLibrary = new Set(rows.map((row) => row.name));
    return STARTERS.map((starter) => ({
      id: starter.id,
      name: starter.label,
      description: starter.summary,
      byline: 'by OpenCompany',
      verified: true,
      meta: meta(starter),
      tile: { tone: starter.role },
      state:
        installing === starter.id ? 'busy' : starter.skills.every((name) => inLibrary.has(name)) ? 'added' : 'available',
    }));
  }, [rows, installing]);

  const install = async (item: CatalogItem) => {
    const starter = STARTERS.find((candidate) => candidate.id === item.id);
    if (!starter || installing) return;
    if (busy) {
      pillToast('Finish the setup you are working on first', { tone: 'info' });
      return;
    }
    setInstalling(starter.id);
    try {
      // The cards show before both lists arrive; an early click waits for them.
      const [libraryRows, builtIns] = await Promise.all([
        library.data ?? library.refetch().then((result) => result.data ?? []),
        discover.data ?? discover.refetch().then((result) => result.data ?? []),
      ]);
      for (const name of starter.skills) {
        const row = libraryRows.find((candidate) => candidate.name === name);
        if (row) {
          if (!row.is_active) await actions.setOn(name, true);
          continue;
        }
        const skill = builtIns.find((candidate) => candidate.skillName === name);
        if (!skill) throw new Error(`${starter.label} needs a skill that isn't available`);
        await actions.add(skill);
      }
    } catch (error) {
      pillToast(error instanceof Error ? error.message : `Couldn't install ${starter.label}`, { tone: 'error' });
      setInstalling(null);
      return;
    }
    setInstalling(null);
    pillToast(`${starter.label} installed`);
    // The draft appears on the hire view while the setup is written.
    void start(starter.job);
    const home = useHomeStore.getState();
    home.closeSettings();
    home.showHire();
  };

  return (
    <CatalogLayout
      title="Plugins"
      searchPlaceholder="Search plugins"
      loading={library.isPending || discover.isPending}
      yours={{
        items: items.filter((item) => item.state === 'added'),
        sectionTitle: 'Installed',
        empty: { title: 'No plugins installed', detail: 'Plugins bundle skills, apps and a routine for a line of work.' },
      }}
      discover={{ items, sectionTitle: 'Top plugins' }}
      verbs={{ add: 'Install', busy: 'Installing…' }}
      onAdd={(item) => void install(item)}
    />
  );
}

export default PluginsTab;
