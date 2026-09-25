/**
 * The Home header's sun/moon button (design handoff "theme button"): flips
 * between light and dark, revealing the new theme as a circle grown from
 * the button while the button spins once with a squash. Home shows only
 * those two themes; the full theme menu stays in Dev mode.
 */

import { Moon, Sun } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { isDarkTheme, useTheme } from '@/contexts/ThemeContext';
import { animate } from '@/lib/motion';
import { SPIKE, spikeOrb } from '../orb/orb';

const SPIN: Keyframe[] = [
  { transform: 'rotate(0deg) scale(1)' },
  { transform: 'rotate(200deg) scale(.78)', offset: 0.45 },
  { transform: 'rotate(360deg) scale(1)' },
];

export function ThemeButton() {
  const { theme, toggleTheme } = useTheme();
  const dark = isDarkTheme(theme);
  const label = dark ? 'Switch to light mode' : 'Switch to dark mode';
  return (
    <Button
      variant="quiet"
      size="icon"
      aria-label={label}
      title={label}
      className="size-8.5 rounded-pill border-border-default"
      onClick={(event) => {
        const rect = event.currentTarget.getBoundingClientRect();
        animate(event.currentTarget, SPIN, { duration: 'theme-icon', easing: 'spring', fill: 'none' });
        spikeOrb(SPIKE.theme);
        toggleTheme({ reveal: true, origin: { x: rect.left + rect.width / 2, y: rect.top + rect.height / 2 } });
      }}
    >
      {dark ? <Sun aria-hidden strokeWidth={1.75} /> : <Moon aria-hidden strokeWidth={1.75} />}
    </Button>
  );
}

export default ThemeButton;
