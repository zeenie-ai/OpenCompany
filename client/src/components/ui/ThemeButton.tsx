/**
 * The theme button (design handoff "theme button"), the same in the Home
 * header, the editor's toolbar and the sign-in screen: each press switches
 * between Light and Dark, revealing the new theme as a circle grown from
 * the button while the button spins once with a squash. The icon and label
 * name the theme a press switches to.
 */

import type { ComponentProps } from 'react';
import { Moon, Sun, type LucideIcon } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { THEME_LABEL, nextTheme, useTheme, type ThemeName } from '@/contexts/ThemeContext';
import { SPIKE, spikeOrb } from '@/features/home/orb/orb';
import { animate } from '@/lib/motion';

const SPIN: Keyframe[] = [
  { transform: 'rotate(0deg) scale(1)' },
  { transform: 'rotate(200deg) scale(.78)', offset: 0.45 },
  { transform: 'rotate(360deg) scale(1)' },
];

const ICON: Record<ThemeName, LucideIcon> = { light: Sun, dark: Moon };

type ThemeButtonProps = Pick<ComponentProps<typeof Button>, 'variant' | 'size' | 'className'>;

/** Home's look by default; the editor's toolbar passes its own. */
export function ThemeButton({
  variant = 'quiet',
  size = 'icon',
  className = 'size-8.5 rounded-pill border-border-default',
}: ThemeButtonProps) {
  const { theme, cycleTheme } = useTheme();
  const next = nextTheme(theme);
  const Icon = ICON[next];
  const label = `Switch to ${THEME_LABEL[next]}`;
  return (
    <Button
      variant={variant}
      size={size}
      aria-label={label}
      title={label}
      className={className}
      onClick={(event) => {
        const rect = event.currentTarget.getBoundingClientRect();
        animate(event.currentTarget, SPIN, { duration: 'theme-icon', easing: 'spring', fill: 'none' });
        spikeOrb(SPIKE.theme);
        cycleTheme({ reveal: true, origin: { x: rect.left + rect.width / 2, y: rect.top + rect.height / 2 } });
      }}
    >
      <Icon aria-hidden strokeWidth={1.75} />
    </Button>
  );
}

export default ThemeButton;
