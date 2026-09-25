/**
 * Settings > Skills (design handoff "Settings: Billing, Skills, Plugins"),
 * on the shared catalog page. Yours is the owner's library: each skill is
 * on or off for employees hired from now on. Discover lists the built-in
 * skills written for AI employees. "Create" writes a new skill in plain
 * words. Changes never reach an employee already hired (data/skills.ts).
 */

import { useMemo, useState } from 'react';
import { ActionButton } from '@/components/ui/action-button';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Textarea } from '@/components/ui/textarea';
import { skillSummary, skillTitle, useDiscoverSkills, useSkillActions, useSkillLibrary } from '../data/skills';
import { pillToast } from '../ui/pillToast';
import type { CatalogItem } from './catalog';
import { CatalogLayout } from './CatalogLayout';

const FIELD = 'bg-bg-app text-row font-normal md:text-row dark:bg-bg-app';
const MAX_TITLE = 60;
const MAX_INSTRUCTIONS = 4000;

function errorText(error: unknown, fallback: string): string {
  return error instanceof Error && error.message ? error.message : fallback;
}

function CreateSkillForm({ close, onCreate }: { close: () => void; onCreate: (title: string, instructions: string) => Promise<void> }) {
  const [title, setTitle] = useState('');
  const [instructions, setInstructions] = useState('');
  const [saving, setSaving] = useState(false);
  const ready = title.trim() !== '' && instructions.trim() !== '' && !saving;

  const submit = async () => {
    if (!ready) return;
    setSaving(true);
    try {
      await onCreate(title.trim(), instructions.trim());
      close();
    } catch (error) {
      pillToast(errorText(error, "Couldn't create that skill"), { tone: 'error' });
      setSaving(false);
    }
  };

  return (
    <>
      <span className="text-base font-semibold text-fg-default">New skill</span>
      <Input
        value={title}
        onChange={(event) => setTitle(event.target.value)}
        onKeyDown={(event) => event.key === 'Enter' && void submit()}
        maxLength={MAX_TITLE}
        placeholder="Name it, e.g. Answer like our front desk"
        aria-label="Skill name"
        className={`h-9.5 rounded-lg ${FIELD}`}
      />
      <Textarea
        value={instructions}
        onChange={(event) => setInstructions(event.target.value)}
        maxLength={MAX_INSTRUCTIONS}
        rows={3}
        placeholder="Explain how it’s done, in plain words. Paste your own steps or examples."
        aria-label="How it’s done"
        className={`min-h-20 rounded-lg px-3 py-2.5 leading-normal ${FIELD}`}
      />
      <div className="flex justify-end gap-2">
        <Button variant="quiet" size="sm" onClick={close}>
          Cancel
        </Button>
        <ActionButton intent="run" disabled={!ready} onClick={() => void submit()} className="h-7.5 rounded-lg px-3.5 text-meta">
          {saving ? 'Adding…' : 'Add'}
        </ActionButton>
      </div>
    </>
  );
}

export function SkillsTab() {
  const library = useSkillLibrary();
  const discover = useDiscoverSkills();
  const actions = useSkillActions();
  const [adding, setAdding] = useState<ReadonlySet<string>>(new Set());

  const rows = useMemo(() => library.data ?? [], [library.data]);
  const builtIns = useMemo(() => discover.data ?? [], [discover.data]);
  const builtInByName = useMemo(() => new Map(builtIns.map((skill) => [skill.skillName, skill])), [builtIns]);

  const yours = useMemo<CatalogItem[]>(
    () =>
      rows.map((row) => {
        const builtIn = builtInByName.has(row.name);
        return {
          id: row.name,
          name: row.display_name || row.name,
          description: skillSummary(row),
          byline: builtIn ? 'by OpenCompany' : 'by you',
          verified: builtIn,
          meta: row.is_active ? 'New hires get this skill' : 'Off for new hires',
          tile: { iconRef: row.icon || null, tone: 'tool' },
          state: 'added',
          enabled: row.is_active,
        };
      }),
    [rows, builtInByName],
  );

  const discoverItems = useMemo<CatalogItem[]>(() => {
    const on = new Set(rows.filter((row) => row.is_active).map((row) => row.name));
    return builtIns.map((skill) => ({
      id: skill.skillName,
      name: skillTitle(skill),
      description: skillSummary(skill),
      byline: 'by OpenCompany',
      verified: true,
      tile: { iconRef: skill.icon || null, tone: 'tool' },
      state: adding.has(skill.skillName) ? 'busy' : on.has(skill.skillName) ? 'added' : 'available',
    }));
  }, [builtIns, rows, adding]);

  const add = async (item: CatalogItem) => {
    const skill = builtInByName.get(item.id);
    if (!skill) return;
    setAdding((names) => new Set(names).add(item.id));
    try {
      await actions.add(skill);
    } catch (error) {
      pillToast(errorText(error, `Couldn't add ${item.name}`), { tone: 'error' });
    } finally {
      setAdding((names) => {
        const next = new Set(names);
        next.delete(item.id);
        return next;
      });
    }
  };

  const remove = async (item: CatalogItem) => {
    try {
      await actions.remove(item.id);
      pillToast(`${item.name} removed`, { tone: 'info' });
    } catch (error) {
      pillToast(errorText(error, `Couldn't remove ${item.name}`), { tone: 'error' });
    }
  };

  const toggle = async (item: CatalogItem, on: boolean) => {
    try {
      await actions.setOn(item.id, on);
    } catch (error) {
      pillToast(errorText(error, `Couldn't change ${item.name}`), { tone: 'error' });
    }
  };

  const create = async (title: string, instructions: string) => {
    await actions.create(title, instructions);
    pillToast(`${title} created`);
  };

  return (
    <CatalogLayout
      title="Skills"
      searchPlaceholder="Search skills"
      loading={library.isPending || discover.isPending}
      yours={{
        items: yours,
        sectionTitle: 'Your skills',
        empty: { title: 'No skills yet', detail: 'Add a ready-made skill or write your own.' },
      }}
      discover={{ items: discoverItems, sectionTitle: 'Popular skills' }}
      verbs={{ add: 'Add', remove: 'Remove', busy: 'Adding…' }}
      onAdd={(item) => void add(item)}
      onRemove={(item) => void remove(item)}
      onToggle={(item, on) => void toggle(item, on)}
      onItemAdded={(item) => pillToast(`${item.name} added to your skills`)}
      primaryAction={{ label: 'Create', form: (close) => <CreateSkillForm close={close} onCreate={create} /> }}
    />
  );
}

export default SkillsTab;
