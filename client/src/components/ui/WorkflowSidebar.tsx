import React from 'react';
import { FolderOpen, FilePlus, Code2, X } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { Card } from '@/components/ui/card';
import { cn } from '@/lib/utils';

export interface SavedWorkflow {
  id: string;
  name: string;
  createdAt: Date;
  lastModified: Date;
  nodeCount: number;
}

interface WorkflowSidebarProps {
  workflows: SavedWorkflow[];
  currentWorkflowId?: string;
  onSelectWorkflow: (workflow: SavedWorkflow) => void;
  onDeleteWorkflow?: (id: string) => void;
}

const formatDate = (date: Date) =>
  new Intl.DateTimeFormat('en-US', {
    month: 'short',
    day: 'numeric',
    hour: '2-digit',
    minute: '2-digit',
  }).format(date);

interface WorkflowCardProps {
  workflow: SavedWorkflow;
  isSelected: boolean;
  onSelect: () => void;
  onDelete?: () => void;
}

const WorkflowCard: React.FC<WorkflowCardProps> = ({
  workflow,
  isSelected,
  onSelect,
  onDelete,
}) => (
  // Card: bg-bg-app (page-level surface, sits below panel chrome) +
  // border-default. Selected state lifts to bg-bg-active and gains a
  // 3px accent left edge — matches handoff `.wf-card.selected`.
  <Card
    onClick={onSelect}
    className={cn(
      'group relative mb-2 cursor-pointer border-border-default bg-bg-app p-3 transition-colors',
      isSelected
        ? 'border-accent border-l-[3px] bg-bg-active'
        : 'hover:bg-bg-hover'
    )}
  >
    <div className="mb-1 flex items-center gap-2">
      <div
        className={cn(
          'flex h-6 w-6 shrink-0 items-center justify-center rounded-sm border',
          isSelected
            ? 'border-accent bg-accent/20 text-accent'
            : 'border-border-default bg-bg-elevated text-fg-muted'
        )}
      >
        <Code2 className="h-3 w-3" />
      </div>
      <h4
        className={cn(
          // Display typography on the workflow name — Cinzel under
          // Renaissance, Major Mono Display under Cyber.
          'm-0 flex-1 truncate font-display text-sm font-medium tracking-[var(--type-tracking-display)] [text-transform:var(--type-uppercase)]',
          isSelected ? 'text-accent' : 'text-fg-default'
        )}
      >
        {workflow.name}
      </h4>
    </div>

    {/* Metadata uses font-mono — picks up IM Fell English / JetBrains
        Mono / system mono per theme. */}
    <div
      className={cn(
        'flex items-center justify-between font-mono text-xs',
        isSelected ? 'text-fg-default' : 'text-fg-muted'
      )}
    >
      <span>{workflow.nodeCount} nodes</span>
      <span>{formatDate(workflow.lastModified)}</span>
    </div>

    {onDelete && (
      <Button
        variant="ghost"
        size="icon-xs"
        onClick={(e) => {
          e.stopPropagation();
          if (window.confirm(`Delete "${workflow.name}"?`)) {
            onDelete();
          }
        }}
        title="Delete workflow"
        className="absolute top-2 right-2 hidden text-destructive hover:bg-destructive/10 group-hover:flex"
      >
        <X className="h-3 w-3" />
      </Button>
    )}
  </Card>
);

const WorkflowSidebar: React.FC<WorkflowSidebarProps> = ({
  workflows,
  currentWorkflowId,
  onSelectWorkflow,
  onDeleteWorkflow,
}) => {
  return (
    // Sidebar shell: bg-bg-panel matches the handoff `.sidebar` token.
    <div className="flex h-full w-[280px] flex-col overflow-hidden border-r border-border-default bg-bg-panel">
      {/* Header — bg-bg-app drops one elevation step below the panel
          chrome, giving the heading area a subtle "page" backdrop
          that's distinct from the card list. */}
      <div className="border-b border-border-default bg-bg-app px-4 py-5">
        <div className="flex items-center gap-2">
          <div className="flex h-9 w-9 items-center justify-center rounded-md bg-accent/20 text-accent">
            <FolderOpen className="h-4 w-4" />
          </div>
          <div>
            <h3 className="m-0 font-display text-base font-semibold tracking-[var(--type-tracking-display)] text-fg-default [text-transform:var(--type-uppercase)]">
              Workflows
            </h3>
            <p className="m-0 font-mono text-xs text-fg-muted">{workflows.length} saved</p>
          </div>
        </div>
      </div>

      {/* List */}
      <div className="flex-1 overflow-y-auto p-3">
        {workflows.length === 0 ? (
          <div className="px-4 py-12 text-center text-sm text-fg-muted">
            <div className="mx-auto mb-4 flex h-16 w-16 items-center justify-center rounded-lg border-2 border-dashed border-border-default bg-bg-elevated">
              <FilePlus className="h-7 w-7 stroke-1" />
            </div>
            <p className="m-0 font-medium text-fg-default">No workflows yet</p>
            <p className="mt-2 text-xs leading-relaxed">
              Create your first workflow<br />to get started
            </p>
          </div>
        ) : (
          workflows.map((workflow) => (
            <WorkflowCard
              key={workflow.id}
              workflow={workflow}
              isSelected={currentWorkflowId === workflow.id}
              onSelect={() => onSelectWorkflow(workflow)}
              onDelete={onDeleteWorkflow ? () => onDeleteWorkflow(workflow.id) : undefined}
            />
          ))
        )}
      </div>
    </div>
  );
};

export default WorkflowSidebar;
