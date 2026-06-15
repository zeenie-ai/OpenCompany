/**
 * ThemeSwitcher — picks the active visual theme.
 *
 * Renders one DropdownMenu item per theme listed in `AVAILABLE_THEMES`,
 * grouped into System / Utopian / Dystopian sections per the design
 * handoff's dual taxonomy.
 *
 * Adding a new theme: drop a CSS file under client/src/themes/, import
 * it in main.tsx, add the name to `AVAILABLE_THEMES` in ThemeContext,
 * and add an entry to `THEME_META` + the matching `THEME_GROUPS` row
 * below.
 */

import * as React from 'react';
import { Check, Palette } from 'lucide-react';
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from '@/components/ui/dropdown-menu';
import { Button } from '@/components/ui/button';
import { useTheme, type ThemeName } from '../../contexts/ThemeContext';

interface ThemeMeta {
  label: string;
  blurb: string;
}

const THEME_META: Record<ThemeName, ThemeMeta> = {
  light:        { label: 'Light',          blurb: 'Clean default' },
  dark:         { label: 'Dark',           blurb: 'Solarized + Dracula' },
  renaissance:  { label: 'Renaissance',    blurb: 'Illuminated codex' },
  greek:        { label: 'Greek',          blurb: 'Sun-bleached marble agora' },
  edo:          { label: 'Edo',            blurb: 'Washi paper, sumi ink' },
  steampunk:    { label: 'Steampunk',      blurb: 'Riveted brass + leather' },
  atomic:       { label: 'Atomic Modern',  blurb: 'Eames mid-century' },
  cyber:        { label: 'Cyber-Tyranny',  blurb: 'Neon night market' },
  wasteland:    { label: 'Wasteland',      blurb: 'Irradiated scrap + rust' },
  rot:          { label: 'Necromantic Rot',blurb: 'Moss-overgrown crypt' },
  plague:       { label: 'Plague City',    blurb: 'Quarantine notices' },
  surveillance: { label: 'Surveillance',   blurb: 'Institutional CCTV' },
};

interface ThemeGroup {
  heading: string;
  themes: readonly ThemeName[];
}

/** Order of sections + members within each section. Drives the
 *  dropdown layout; AVAILABLE_THEMES flat list is unchanged. */
const THEME_GROUPS: readonly ThemeGroup[] = [
  { heading: 'System',    themes: ['light', 'dark'] },
  { heading: 'Utopian',   themes: ['renaissance', 'greek', 'edo', 'steampunk', 'atomic'] },
  { heading: 'Dystopian', themes: ['cyber', 'wasteland', 'rot', 'plague', 'surveillance'] },
];

export const ThemeSwitcher: React.FC<{ className?: string }> = ({ className }) => {
  const { theme, setTheme } = useTheme();
  const activeMeta = THEME_META[theme];

  return (
    <DropdownMenu>
      <DropdownMenuTrigger asChild>
        <Button
          variant="outline"
          size="icon-sm"
          title={`Theme: ${activeMeta.label}`}
          className="border-action-secret-border bg-action-secret-soft text-action-secret-ink hover:bg-action-secret-hover"
        >
          <Palette />
        </Button>
      </DropdownMenuTrigger>
      <DropdownMenuContent align="end" className={className ?? 'min-w-[240px]'}>
        {THEME_GROUPS.map((group, groupIdx) => (
          <React.Fragment key={group.heading}>
            {groupIdx > 0 && <DropdownMenuSeparator />}
            <DropdownMenuLabel className="text-xs uppercase tracking-wider text-muted-foreground">
              {group.heading}
            </DropdownMenuLabel>
            {group.themes.map((name) => {
              const meta = THEME_META[name];
              const isActive = name === theme;
              return (
                <DropdownMenuItem
                  key={name}
                  onSelect={() => setTheme(name)}
                  className="flex items-center gap-2"
                >
                  <span className="flex h-4 w-4 items-center justify-center">
                    {isActive && <Check className="h-3.5 w-3.5" />}
                  </span>
                  <span className="flex-1">
                    <span className="block text-sm font-medium">{meta.label}</span>
                    <span className="block text-[11px] text-muted-foreground">{meta.blurb}</span>
                  </span>
                </DropdownMenuItem>
              );
            })}
          </React.Fragment>
        ))}
      </DropdownMenuContent>
    </DropdownMenu>
  );
};

export default ThemeSwitcher;
