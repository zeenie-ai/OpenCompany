/** Shared catalogue brand marks, avatar faces and search fields. */
import { useState } from 'react';
import { Search } from 'lucide-react';
import { NodeIcon } from '@/assets/icons';
import { Input } from '@/components/ui/input';
import { cn } from '@/lib/utils';
import { theme } from '@/styles/theme';
import { AVATAR_CLASS, initialOf, type ColorRole } from './presentation';

/** What an avatar disc shows: the photo when one is given and loads, else
 *  the first letter of the name. */
export function AvatarFace({ name, photo }: { name: string; photo?: string | null }) {
  const [broken, setBroken] = useState<string | null>(null);
  if (photo && broken !== photo) {
    return <img src={photo} alt="" draggable={false} onError={() => setBroken(photo)} className="size-full rounded-full object-cover" />;
  }
  return <>{initialOf(name)}</>;
}

const MARK_SIZE = {
  xs: { box: 'size-5', icon: theme.iconSize.xs },
  sm: { box: 'size-5.5', icon: theme.iconSize.xs },
  lg: { box: 'size-9.5 rounded-row', icon: theme.iconSize.md },
  xl: { box: 'size-12.5 rounded-card text-sm', icon: theme.iconSize.lg },
} as const;

/** An app's brand mark (the credential catalogue's icon), in a disc. Falls
 *  back to the app's first two letters, tinted in `tone` when given (a
 *  skill or starter tile). */
export function AppMark({
  name,
  iconRef,
  size = 'sm',
  tone,
  className,
}: {
  name: string;
  iconRef?: string | null;
  size?: keyof typeof MARK_SIZE;
  tone?: ColorRole;
  className?: string;
}) {
  const spec = MARK_SIZE[size];
  const letters = name.replace(/[^\p{L}\p{N}]/gu, '').slice(0, 2).toUpperCase();
  return (
    <span
      aria-hidden
      className={cn(
        'grid shrink-0 place-items-center rounded-full border font-mono text-2xs font-semibold',
        tone ? AVATAR_CLASS[tone] : 'border-border-default bg-bg-panel text-fg-muted',
        spec.box,
        className,
      )}
    >
      <NodeIcon icon={iconRef} size={spec.icon} fallback={<span>{letters}</span>} />
    </span>
  );
}

/** A search box for settings and catalogue surfaces. The
 *  placeholder doubles as its accessible name unless `label` says otherwise. */
export function SearchField({
  value,
  onChange,
  placeholder,
  label,
  className,
}: {
  value: string;
  onChange: (value: string) => void;
  placeholder: string;
  label?: string;
  className?: string;
}) {
  return (
    <div className={cn('relative min-w-0', className)}>
      <Search aria-hidden className="pointer-events-none absolute top-1/2 left-3 size-3.75 -translate-y-1/2 text-fg-faint" />
      <Input
        value={value}
        onChange={(event) => onChange(event.target.value)}
        placeholder={placeholder}
        aria-label={label ?? placeholder}
        className="h-9 rounded-row bg-bg-app pl-8.5 text-row font-normal md:text-row dark:bg-bg-app"
      />
    </div>
  );
}
