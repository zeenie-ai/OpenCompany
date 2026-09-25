/**
 * Settings > Skills: "+" copies a built-in's text into the library; the
 * Yours switch turns a skill on or off for new hires; Remove deletes it;
 * Create gives a new skill a name of its own, never a taken or built-in
 * one. Plus the naming and summary helpers, and the shared folder fetcher.
 */

import { beforeEach, describe, expect, it, vi } from 'vitest';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import type { UserSkill } from '@/hooks/useUserSkills';

type Row = UserSkill;
let library: Row[] = [];
const builtIns = [
  {
    name: 'book-appointments',
    description: 'Use when someone wants to book.',
    metadata: { title: 'Book appointments', summary: 'Offers free times.', icon: 'lucide:CalendarCheck', category: 'scheduling' },
  },
  {
    name: 'short-reports',
    description: 'Use when reporting.',
    metadata: { title: 'Short reports', summary: 'Reports you read fast.', category: 'reporting' },
  },
];

const sendRequest = vi.fn(async (type: string, data: Record<string, any> = {}) => {
  switch (type) {
    case 'get_user_skills':
      return { skills: library };
    case 'scan_skill_folder':
      return { success: true, skills: builtIns };
    case 'get_skill_content':
      return { success: true, instructions: `# ${data.skill_name}\nDo it well.` };
    case 'lookup_skill_metadata':
      return { success: true, skills: builtIns.filter((skill) => data.names.includes(skill.name)) };
    case 'create_user_skill': {
      const row: Row = {
        name: data.name,
        display_name: data.display_name,
        description: data.description,
        instructions: data.instructions,
        icon: data.icon ?? 'star',
        color: '',
        category: data.category,
        is_active: true,
        metadata: data.metadata,
      };
      library = [...library, row];
      return { skill: row };
    }
    case 'update_user_skill':
      library = library.map((row) => (row.name === data.name ? { ...row, is_active: data.is_active } : row));
      return { skill: library.find((row) => row.name === data.name) };
    case 'delete_user_skill':
      library = library.filter((row) => row.name !== data.name);
      return { success: true };
    default:
      return {};
  }
});

vi.mock('@/contexts/WebSocketContext', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/contexts/WebSocketContext')>()),
  useWebSocketActions: () => ({ sendRequest, isReady: true, addEventListener: () => () => {} }),
}));
vi.mock('../ui/pillToast', () => ({ pillToast: vi.fn() }));

import { ThemeProvider } from '@/contexts/ThemeContext';
import { fetchFolderSkills } from '@/hooks/useFolderSkills';
import { firstSentence, librarySkillName } from '../data/skills';
import { SkillsTab } from '../settings/SkillsTab';

function row(name: string, patch: Partial<Row> = {}): Row {
  return { name, display_name: name, description: '', instructions: 'x', icon: '', color: '', category: 'custom', is_active: true, ...patch };
}

function renderTab() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <ThemeProvider>
      <QueryClientProvider client={client}>
        <SkillsTab />
      </QueryClientProvider>
    </ThemeProvider>,
  );
}

beforeEach(() => {
  library = [];
  sendRequest.mockClear();
});

describe('SkillsTab', () => {
  it('adds a built-in by copying its text into the library', async () => {
    renderTab();
    fireEvent.click(await screen.findByRole('button', { name: 'Add Book appointments' }));
    await waitFor(() =>
      expect(sendRequest).toHaveBeenCalledWith(
        'create_user_skill',
        expect.objectContaining({
          name: 'book-appointments',
          display_name: 'Book appointments',
          instructions: '# book-appointments\nDo it well.',
        }),
      ),
    );
    expect(sendRequest).toHaveBeenCalledWith('get_skill_content', { skill_name: 'book-appointments' });
    expect(await screen.findByRole('button', { name: 'Remove Book appointments' })).toBeInTheDocument();
  });

  it('switches a skill off for new hires, and removes one', async () => {
    library = [row('short-reports', { display_name: 'Short reports' })];
    renderTab();
    fireEvent.click(await screen.findByRole('radio', { name: /Yours/ }));
    fireEvent.click(await screen.findByRole('switch', { name: 'Short reports' }));
    await waitFor(() => expect(sendRequest).toHaveBeenCalledWith('update_user_skill', { name: 'short-reports', is_active: false }));
    fireEvent.click(screen.getByRole('button', { name: 'Remove Short reports' }));
    await waitFor(() => expect(sendRequest).toHaveBeenCalledWith('delete_user_skill', { name: 'short-reports' }));
  });

  it('creates a skill under a name of its own', async () => {
    library = [row('front-desk')];
    renderTab();
    fireEvent.click(await screen.findByRole('button', { name: 'Create' }));
    const add = screen.getByRole('button', { name: 'Add' });
    expect(add).toBeDisabled();
    fireEvent.change(screen.getByRole('textbox', { name: 'Skill name' }), { target: { value: 'Front desk' } });
    fireEvent.change(screen.getByRole('textbox', { name: 'How it’s done' }), {
      target: { value: 'Greet everyone by name. Keep it short.' },
    });
    fireEvent.click(add);
    await waitFor(() =>
      expect(sendRequest).toHaveBeenCalledWith(
        'create_user_skill',
        expect.objectContaining({
          name: 'front-desk-2',
          display_name: 'Front desk',
          description: 'Front desk: Greet everyone by name.',
          metadata: { title: 'Front desk', summary: 'Greet everyone by name.' },
        }),
      ),
    );
  });
});

describe('skill helpers', () => {
  it('names a new skill by its title, past taken, built-in and reserved names', async () => {
    const send = vi.fn(async (_type: string, data: Record<string, any> = {}) => ({
      success: true,
      skills: data.names.includes('book-appointments') ? [{ name: 'book-appointments' }] : [],
    }));
    expect(await librarySkillName(send as never, 'Front Desk!', [])).toBe('front-desk');
    expect(await librarySkillName(send as never, 'Front desk', [row('front-desk')])).toBe('front-desk-2');
    expect(await librarySkillName(send as never, 'Book appointments', [])).toBe('book-appointments-2');
    expect(await librarySkillName(send as never, 'Pirate personality', [])).toBe('pirate-personality-2');
    expect(await librarySkillName(send as never, '!!!', [])).toBe('my-skill');
  });

  it('takes the first sentence of plain-words instructions', () => {
    expect(firstSentence('Greet people by name. Then help.')).toBe('Greet people by name.');
    expect(firstSentence('\n# Front desk\nGreet people.')).toBe('Front desk');
    expect(firstSentence('')).toBe('');
  });

  it('keeps a folder skill’s metadata', async () => {
    const send = async () => ({ success: true, skills: [{ name: 'a-b', description: 'd', metadata: { title: 'T' } }] });
    const [skill] = await fetchFolderSkills(send as never, 'employee');
    expect(skill).toMatchObject({ skillName: 'a-b', displayName: 'A B', metadata: { title: 'T' } });
  });
});
