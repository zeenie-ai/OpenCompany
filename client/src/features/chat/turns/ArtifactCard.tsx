/**
 * A document the employee wrote on its Canvas, in the reply that wrote it
 * (design handoff chat, "artifact card"): its title, what it is and which
 * version, and Open, which shows it in the host's Canvas (Home's Workspace,
 * the editor's Canvas dock) at that version.
 */

import { FileText } from 'lucide-react';
import { cn } from '@/lib/utils';
import type { ArtifactRef } from '../data/parts';

export function ArtifactCard({ artifact, onOpen }: { artifact: ArtifactRef; onOpen?: (artifact: ArtifactRef) => void }) {
  const kind = artifact.format === 'code' ? 'Code' : 'Document';
  const meta = artifact.version > 1 ? `${kind} · version ${artifact.version}` : kind;
  return (
    <button
      type="button"
      data-artifact={artifact.itemId}
      disabled={!onOpen}
      onClick={() => onOpen?.(artifact)}
      aria-label={`Open ${artifact.title}`}
      className={cn(
        'flex w-full max-w-105 items-center gap-3 rounded-card border border-border-default bg-bg-panel px-3.5 py-3 text-left text-fg-default transition-[border-color,transform,box-shadow] duration-(--dur-default)',
        onOpen && 'hover:-translate-y-px hover:border-border-strong hover:shadow-card-hover motion-reduce:hover:translate-y-0',
      )}
    >
      <span aria-hidden className="grid size-9.5 flex-none place-items-center rounded-lg border border-border-default bg-bg-active text-fg-muted">
        <FileText className="size-4.25" strokeWidth={1.8} />
      </span>
      <span className="flex min-w-0 flex-1 flex-col gap-px">
        <span className="truncate text-sm font-semibold">{artifact.title}</span>
        <span className="text-xs text-fg-muted">{meta}</span>
      </span>
      {onOpen && <span className="text-xs font-medium text-fg-muted">Open</span>}
    </button>
  );
}

export default ArtifactCard;
