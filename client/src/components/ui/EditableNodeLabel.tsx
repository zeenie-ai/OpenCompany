/**
 * EditableNodeLabel — shared inline-rename input for canvas node components.
 *
 * Encapsulates the dup'd pattern across StartNode, SquareNode, TriggerNode,
 * ToolkitNode:
 *   - sync local edit state with the global `useAppStore.renamingNodeId`
 *   - focus + select the input on enter
 *   - Enter saves, Escape cancels, blur saves
 *   - call updateNodeData with the new label (no-op when unchanged or empty)
 *
 * Render is two states:
 *   - editing: an <Input> wired to the rename keymap
 *   - idle:    a div that calls onActivate on double-click (the parent owns
 *              the activation gesture; it usually pipes into setRenamingNodeId)
 */

import React, { useEffect, useRef, useState, useCallback } from 'react';
import { Input } from '@/components/ui/input';
import { cn } from '@/lib/utils';
import { useAppStore } from '../../store/useAppStore';

export interface EditableNodeLabelProps {
  /** React Flow node id; the global rename state is keyed on this. */
  nodeId: string;
  /** Current label to display. Falls back to `defaultLabel` when empty. */
  label: string | undefined;
  /** Fallback label text rendered when no label is set. */
  defaultLabel: string;
  /** Persists the new label (only fired when changed and non-empty). */
  onLabelChange: (newLabel: string) => void;
  /** Called when the user double-clicks the idle label. Usually
   *  `() => setRenamingNodeId(nodeId)` in the parent. */
  onActivate?: () => void;
  /** Extra Tailwind classes for the rendered input + idle div. */
  className?: string;
}

const EditableNodeLabel: React.FC<EditableNodeLabelProps> = ({
  nodeId,
  label,
  defaultLabel,
  onLabelChange,
  onActivate,
  className,
}) => {
  const renamingNodeId = useAppStore((s) => s.renamingNodeId);
  const setRenamingNodeId = useAppStore((s) => s.setRenamingNodeId);

  const [isRenaming, setIsRenaming] = useState(false);
  const [editLabel, setEditLabel] = useState('');
  const inputRef = useRef<HTMLInputElement>(null);

  // Sync with global rename state.
  useEffect(() => {
    if (renamingNodeId === nodeId) {
      setIsRenaming(true);
      setEditLabel(label || defaultLabel);
    } else {
      setIsRenaming(false);
    }
  }, [renamingNodeId, nodeId, label, defaultLabel]);

  // Focus + select on enter.
  useEffect(() => {
    if (isRenaming && inputRef.current) {
      inputRef.current.focus();
      inputRef.current.select();
    }
  }, [isRenaming]);

  const handleSave = useCallback(() => {
    const newLabel = editLabel.trim();
    const original = label || defaultLabel;
    if (newLabel && newLabel !== original) {
      onLabelChange(newLabel);
    }
    setIsRenaming(false);
    setRenamingNodeId(null);
  }, [editLabel, label, defaultLabel, onLabelChange, setRenamingNodeId]);

  const handleCancel = useCallback(() => {
    setIsRenaming(false);
    setRenamingNodeId(null);
  }, [setRenamingNodeId]);

  if (isRenaming) {
    return (
      <Input
        ref={inputRef}
        type="text"
        value={editLabel}
        onChange={(e) => setEditLabel(e.target.value)}
        onKeyDown={(e) => {
          if (e.key === 'Enter') handleSave();
          else if (e.key === 'Escape') handleCancel();
          e.stopPropagation();
        }}
        onBlur={handleSave}
        onClick={(e) => e.stopPropagation()}
        className={cn(
          // Both `sq-node-label` and `node-label` co-classes always emit so
          // per-theme CSS rules fire for whichever parent topology this
          // label sits inside (`.sq-node` for square, `.node` for
          // rectangular). Wave 26.B.
          'sq-node-label node-label mt-2 h-auto max-w-[120px] border-accent px-1 py-0.5 text-center text-xs font-medium',
          className
        )}
      />
    );
  }

  return (
    <div
      onDoubleClick={onActivate}
      title="Double-click to rename"
      className={cn(
        // Both `sq-node-label` and `node-label` co-classes always emit so
        // per-theme CSS rules fire for whichever parent topology this
        // label sits inside (`.sq-node` for square, `.node` for
        // rectangular). Wave 26.B.
        'sq-node-label node-label mt-2 max-w-[120px] cursor-text text-center text-xs leading-tight font-medium text-foreground',
        className
      )}
    >
      {label || defaultLabel}
    </div>
  );
};

export default EditableNodeLabel;
