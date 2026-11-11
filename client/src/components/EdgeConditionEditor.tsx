/**
 * EdgeConditionEditor - Modal for configuring edge conditions
 *
 * Allows users to set up runtime conditional branching on workflow edges.
 * Conditions are evaluated against the source node's output at runtime.
 */
import React, { useState, useEffect, useCallback } from 'react';
import { GitBranch } from 'lucide-react';
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
  DialogDescription,
  DialogFooter,
} from '@/components/ui/dialog';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { Button } from '@/components/ui/button';
import { Alert, AlertDescription } from '@/components/ui/alert';
import { Card } from '@/components/ui/card';
import {
  Select,
  SelectContent,
  SelectGroup,
  SelectItem,
  SelectLabel,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select';
import {
  EdgeCondition,
  ConditionOperator,
  OPERATORS,
  getOperatorsByCategory,
  formatConditionLabel,
  validateCondition,
} from '../types/EdgeCondition';

interface EdgeConditionEditorProps {
  isOpen: boolean;
  onClose: () => void;
  /** Current condition (if editing existing) */
  condition?: EdgeCondition;
  /** Current edge label */
  label?: string;
  /** Callback when condition is saved */
  onSave: (condition: EdgeCondition | undefined, label: string | undefined) => void;
  /** Available fields from source node output (for autocomplete) */
  availableFields?: string[];
  /** Source node name for display */
  sourceNodeName?: string;
}

const parseValue = (val: string, type?: string): any => {
  if (type === 'number') {
    const num = parseFloat(val);
    return isNaN(num) ? val : num;
  }
  if (type === 'boolean') return val.toLowerCase() === 'true';
  if (type === 'array') {
    try {
      return JSON.parse(val);
    } catch {
      return val.split(',').map(s => s.trim());
    }
  }
  return val;
};

const EdgeConditionEditor: React.FC<EdgeConditionEditorProps> = ({
  isOpen,
  onClose,
  condition,
  label,
  onSave,
  availableFields = [],
  sourceNodeName,
}) => {
  // Form state
  const [field, setField] = useState(condition?.field || '');
  const [operator, setOperator] = useState<ConditionOperator>(condition?.operator || 'eq');
  const [value, setValue] = useState<string>(
    condition?.value !== undefined ? String(condition.value) : ''
  );
  const [edgeLabel, setEdgeLabel] = useState(label || '');
  const [error, setError] = useState<string | null>(null);

  // Reset form when modal opens with new condition
  useEffect(() => {
    if (isOpen) {
      setField(condition?.field || '');
      setOperator(condition?.operator || 'eq');
      setValue(condition?.value !== undefined ? String(condition.value) : '');
      setEdgeLabel(label || '');
      setError(null);
    }
  }, [isOpen, condition, label]);

  const operatorMeta = OPERATORS[operator];
  const operatorsByCategory = getOperatorsByCategory();

  const handleSave = useCallback(() => {
    if (!field.trim()) {
      onSave(undefined, edgeLabel.trim() || undefined);
      onClose();
      return;
    }

    const newCondition: EdgeCondition = {
      field: field.trim(),
      operator,
      value: operatorMeta.requiresValue ? parseValue(value, operatorMeta.valueType) : undefined,
    };

    const validation = validateCondition(newCondition);
    if (!validation.valid) {
      setError(validation.error || 'Invalid condition');
      return;
    }

    onSave(newCondition, edgeLabel.trim() || formatConditionLabel(newCondition));
    onClose();
  }, [field, operator, value, edgeLabel, operatorMeta, onSave, onClose]);

  const handleRemoveCondition = useCallback(() => {
    onSave(undefined, undefined);
    onClose();
  }, [onSave, onClose]);

  return (
    <Dialog open={isOpen} onOpenChange={(open) => !open && onClose()}>
      {/* `sm:`: the primitive's own `sm:max-w-sm` beats an unprefixed width. */}
      <DialogContent className="max-h-[85vh] w-[420px] overflow-hidden p-0 sm:max-w-[420px]">
        <DialogHeader className="border-b border-border bg-muted px-5 py-4">
          <DialogTitle className="flex items-center gap-2">
            <GitBranch className="h-4 w-4 text-fg-muted" />
            Edge Condition
          </DialogTitle>
          <DialogDescription className="sr-only">
            Configure condition for edge branching
          </DialogDescription>
        </DialogHeader>

        {/* Content */}
        <div className="space-y-4 overflow-y-auto px-5 py-5">
          {sourceNodeName && (
            <Card className="bg-muted px-3 py-2 text-sm text-muted-foreground">
              Condition evaluates output from:{' '}
              <strong className="text-foreground">{sourceNodeName}</strong>
            </Card>
          )}

          {/* Field input */}
          <div className="space-y-1.5">
            <Label htmlFor="edge-field">Field Path</Label>
            <Input
              id="edge-field"
              type="text"
              value={field}
              onChange={(e) => setField(e.target.value)}
              placeholder="e.g., result.status, items.0.name"
              list="available-fields"
            />
            {availableFields.length > 0 && (
              <datalist id="available-fields">
                {availableFields.map((f) => (
                  <option key={f} value={f} />
                ))}
              </datalist>
            )}
            <p className="text-xs text-muted-foreground">
              Use dot notation for nested fields (e.g., result.data.value)
            </p>
          </div>

          {/* Operator select */}
          <div className="space-y-1.5">
            <Label htmlFor="edge-operator">Operator</Label>
            <Select value={operator} onValueChange={(v) => setOperator(v as ConditionOperator)}>
              <SelectTrigger id="edge-operator">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                {Object.entries(operatorsByCategory).map(([category, ops]) => (
                  <SelectGroup key={category}>
                    <SelectLabel>{category}</SelectLabel>
                    {ops.map((op) => (
                      <SelectItem key={op} value={op}>
                        {OPERATORS[op].label}
                      </SelectItem>
                    ))}
                  </SelectGroup>
                ))}
              </SelectContent>
            </Select>
            <p className="text-xs text-muted-foreground">{operatorMeta.description}</p>
          </div>

          {/* Value input (conditional) */}
          {operatorMeta.requiresValue && (
            <div className="space-y-1.5">
              <Label htmlFor="edge-value">
                Value
                {operatorMeta.valueType === 'array' && (
                  <span className="ml-2 font-normal">(comma-separated or JSON array)</span>
                )}
              </Label>
              <Input
                id="edge-value"
                type={operatorMeta.valueType === 'number' ? 'number' : 'text'}
                value={value}
                onChange={(e) => setValue(e.target.value)}
                placeholder={
                  operatorMeta.valueType === 'array'
                    ? 'e.g., value1, value2 or ["a", "b"]'
                    : 'Enter comparison value'
                }
              />
            </div>
          )}

          {/* Edge label */}
          <div className="space-y-1.5">
            <Label htmlFor="edge-label">Edge Label (optional)</Label>
            <Input
              id="edge-label"
              type="text"
              value={edgeLabel}
              onChange={(e) => setEdgeLabel(e.target.value)}
              placeholder="Auto-generated from condition"
            />
            <p className="text-xs text-muted-foreground">Displayed on the workflow canvas</p>
          </div>

          {/* Error message */}
          {error && (
            <Alert variant="destructive">
              <AlertDescription>{error}</AlertDescription>
            </Alert>
          )}

          {/* Preview */}
          {field && (
            <Card className="bg-muted p-3">
              <div className="mb-1 text-xs text-muted-foreground">Preview:</div>
              <div className="font-mono text-sm text-fg-default">
                if output.{field} {OPERATORS[operator].label.toLowerCase()}
                {operatorMeta.requiresValue && value && ` "${value}"`}
              </div>
            </Card>
          )}
        </div>

        {/* Footer */}
        <DialogFooter className="flex flex-row items-center justify-between border-t border-border bg-muted px-5 py-4 sm:justify-between">
          {condition ? (
            <Button variant="destructive" onClick={handleRemoveCondition}>
              Remove Condition
            </Button>
          ) : (
            <div />
          )}
          {/* Colour marks state or role (theme_system.md): Cancel is a
              neutral outline, and Save is the dialog's one strong button. */}
          <div className="flex gap-2">
            <Button variant="quiet" className="border-border-default text-fg-default" onClick={onClose}>
              Cancel
            </Button>
            <Button variant="invert" className="px-3.5 font-semibold" onClick={handleSave}>
              Save Condition
            </Button>
          </div>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
};

export default EdgeConditionEditor;
