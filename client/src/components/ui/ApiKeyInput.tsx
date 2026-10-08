/**
 * ApiKeyInput - Reusable API key input component with central theming
 */

import React, { useState } from 'react';
import { Eye, EyeOff, Save, Trash2, CheckCircle, Loader2 } from 'lucide-react';

import { Input } from '@/components/ui/input';
import { Button } from '@/components/ui/button';

interface ApiKeyInputProps {
  value: string;
  onChange: (value: string) => void;
  onSave: () => void;
  onDelete?: () => void;
  placeholder?: string;
  loading?: boolean;
  isStored?: boolean | null;
  disabled?: boolean;
  saveLabel?: string;
  savedLabel?: string;
}

const ApiKeyInput: React.FC<ApiKeyInputProps> = ({
  value,
  onChange,
  onSave,
  onDelete,
  placeholder = 'Enter API key...',
  loading = false,
  isStored = false,
  disabled = false,
  saveLabel = 'Validate',
  savedLabel = 'Valid',
}) => {
  const [visible, setVisible] = useState(false);

  return (
    <div className="flex w-full items-stretch gap-1">
      <div className="relative flex-1">
        <Input
          type={visible ? 'text' : 'password'}
          value={value}
          onChange={(e) => onChange(e.target.value)}
          placeholder={placeholder}
          disabled={disabled}
          className="font-mono pr-9"
        />
        <button
          type="button"
          onClick={() => setVisible((v) => !v)}
          className="absolute top-1/2 right-2 -translate-y-1/2 text-muted-foreground hover:text-foreground"
          aria-label={visible ? 'Hide key' : 'Show key'}
        >
          {visible ? <EyeOff className="h-4 w-4" /> : <Eye className="h-4 w-4" />}
        </button>
      </div>

      <Button
        variant="default"
        onClick={onSave}
        disabled={!value.trim() || disabled || loading}
      >
        {loading ? (
          <Loader2 className="h-4 w-4 animate-spin" />
        ) : isStored ? (
          <CheckCircle className="h-4 w-4" />
        ) : (
          <Save className="h-4 w-4" />
        )}
        {isStored ? savedLabel : saveLabel}
      </Button>

      {isStored && onDelete && (
        <Button
          variant="destructive"
          size="icon"
          onClick={onDelete}
          disabled={disabled}
          aria-label="Remove key"
          title="Remove key"
        >
          <Trash2 className="h-4 w-4" />
        </Button>
      )}
    </div>
  );
};

export default ApiKeyInput;
