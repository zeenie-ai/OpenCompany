/**
 * The Home header's sun/moon button (design handoff "theme button"): flips
 * between the light and dark families, revealing the new theme as a circle
 * grown from the button. From a stylized theme it goes to the other
 * family's base theme; the full theme menu stays in Dev mode.
 */

import { Moon, Sun } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { isDarkTheme, useTheme } from '@/contexts/ThemeContext';

export function ThemeButton() {
  const { theme, toggleTheme } = useTheme();
  const dark = isDarkTheme(theme);
  const label = dark ? 'Switch to light theme' : 'Switch to dark theme';
  return (
    <Button
      variant="quiet"
      size="icon"
      aria-label={label}
      title={label}
      className="size-8.5 rounded-pill border-border-default"
      onClick={(event) => {
        const rect = event.currentTarget.getBoundingClientRect();
        toggleTheme({ reveal: true, origin: { x: rect.left + rect.width / 2, y: rect.top + rect.height / 2 } });
      }}
    >
      {dark ? <Sun aria-hidden /> : <Moon aria-hidden />}
    </Button>
  );
}

export default ThemeButton;
