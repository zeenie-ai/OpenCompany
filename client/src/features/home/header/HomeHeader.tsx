/**
 * Normal mode's header (design handoff "Main header"): with the sidebar
 * collapsed, an open button and the logo; the view's title; the Normal/Dev
 * switch and the theme button. The bottom border appears only once the
 * content has scrolled, so the hero reads as one surface.
 */

import { PanelLeft } from 'lucide-react';
import { OcLogo } from '@/components/brand/Logo';
import { ModeToggle } from '@/components/shell/ModeToggle';
import { Button } from '@/components/ui/button';
import { cn } from '@/lib/utils';
import { useHomeStore } from '../state/homeStore';
import { ThemeButton } from './ThemeButton';

export function HomeHeader({ title, scrolled }: { title: string; scrolled: boolean }) {
  const sidebarOpen = useHomeStore((s) => s.sidebarOpen);
  const toggleSidebar = useHomeStore((s) => s.toggleSidebar);
  const logoPulse = useHomeStore((s) => s.logoPulse);

  return (
    <header
      className={cn(
        'relative z-10 flex h-(--h-home-header) shrink-0 items-center gap-2 border-b px-3.5 transition-colors duration-(--dur-slow)',
        scrolled ? 'border-border-default' : 'border-transparent',
      )}
    >
      {!sidebarOpen && (
        <>
          <Button variant="quiet" size="icon" onClick={toggleSidebar} aria-label="Open sidebar" title="Open sidebar" className="rounded-lg">
            <PanelLeft className="size-4.25" strokeWidth={1.75} />
          </Button>
          <OcLogo size="header" pulseNonce={logoPulse} className="pr-1.5 pl-0.5" />
          <span aria-hidden className="h-5 w-px bg-border-default" />
        </>
      )}
      <h2 className="truncate px-1.5 text-lead font-semibold text-fg-default">{title}</h2>
      <div className="ml-auto flex shrink-0 items-center gap-2">
        <ModeToggle />
        <ThemeButton />
      </div>
    </header>
  );
}

export default HomeHeader;
