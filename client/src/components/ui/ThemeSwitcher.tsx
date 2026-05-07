/**
 * ThemeSwitcher — picks the active visual theme.
 *
 * Renders one DropdownMenu item per theme listed in `AVAILABLE_THEMES`.
 * Selection persists via ThemeContext (localStorage `machinaos-theme`).
 *
 * Adding a new theme: drop a CSS file under client/src/themes/, import
 * it in main.tsx, add the name to `AVAILABLE_THEMES` in ThemeContext.
 * This component picks it up automatically; no edits here.
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
  light: { label: 'Light', blurb: 'Clean default' },
  dark: { label: 'Dark', blurb: 'Solarized + Dracula' },
  renaissance: { label: 'Renaissance', blurb: 'Illuminated codex' },
  cyber: { label: 'Cyber-Tyranny', blurb: 'Neon night market' },
};

export const ThemeSwitcher: React.FC<{ className?: string }> = ({ className }) => {
  const { theme, setTheme, availableThemes } = useTheme();
  const activeMeta = THEME_META[theme];

  return (
    <DropdownMenu>
      <DropdownMenuTrigger asChild>
        <Button
          variant="outline"
          size="icon-sm"
          title={`Theme: ${activeMeta.label}`}
          className="border-action-secret-border bg-action-secret-soft text-action-secret hover:bg-action-secret/25"
        >
          <Palette />
        </Button>
      </DropdownMenuTrigger>
      <DropdownMenuContent align="end" className={className ?? 'min-w-[220px]'}>
        <DropdownMenuLabel className="text-xs uppercase tracking-wider text-muted-foreground">
          Theme
        </DropdownMenuLabel>
        <DropdownMenuSeparator />
        {availableThemes.map((name) => {
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
      </DropdownMenuContent>
    </DropdownMenu>
  );
};

export default ThemeSwitcher;
