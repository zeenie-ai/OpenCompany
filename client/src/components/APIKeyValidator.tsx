import React, { useEffect } from 'react';
import { CheckCircle, XCircle, Loader2, Trash2 } from 'lucide-react';

import { Input } from '@/components/ui/input';
import { Button } from '@/components/ui/button';
import { Badge } from '@/components/ui/badge';
import { Tooltip, TooltipContent, TooltipProvider, TooltipTrigger } from '@/components/ui/tooltip';
import { useApiKeyValidation } from '../hooks/useApiKeyValidation';
import { cn } from '@/lib/utils';

interface APIKeyValidatorProps {
  requestKey?: string;
  value: string;
  onChange: (value: string) => void;
  placeholder?: string;
  validationConfig: {
    provider?: string;
    showValidateButton?: boolean;
  };
  onValidationSuccess?: (models: string[]) => void;
  isDragOver?: boolean;
  onDragOver?: (e: React.DragEvent) => void;
  onDragLeave?: (e: React.DragEvent) => void;
  onDrop?: (e: React.DragEvent) => void;
}

const APIKeyValidator: React.FC<APIKeyValidatorProps> = ({
  requestKey,
  value,
  onChange,
  placeholder,
  validationConfig,
  onValidationSuccess,
  isDragOver = false,
  onDragOver,
  onDragLeave,
  onDrop
}) => {
  const { status, hasStoredKey, validate, clear, getStoredKey, isValidating, isValid } = useApiKeyValidation({
    provider: validationConfig.provider,
    requestKey,
    onSuccess: onValidationSuccess
  });

  useEffect(() => {
    let cancelled = false;
    if (hasStoredKey && !value) {
      getStoredKey().then((storedKey) => {
        if (!cancelled && storedKey) onChange(storedKey);
      });
    }
    return () => {
      cancelled = true;
    };
  }, [hasStoredKey, value, onChange, getStoredKey, requestKey]);

  const handleValidate = () => validate(value);
  const handleClear = async () => {
    const cleared = await clear();
    if (cleared) onChange('');
  };

  return (
    <div className="flex w-full flex-col gap-1.5">
      <div className="flex w-full items-stretch gap-1">
        <div className="relative flex-1">
          <Input
            type="password"
            value={value}
            onChange={(e) => onChange(e.target.value)}
            placeholder={placeholder || 'Enter API key...'}
            onDragOver={onDragOver}
            onDragLeave={onDragLeave}
            onDrop={onDrop}
            aria-invalid={status === 'invalid' || undefined}
            className={cn(
              'font-mono pr-8',
              isDragOver && 'border-primary bg-primary/10'
            )}
          />
          <div className="pointer-events-none absolute top-1/2 right-2 -translate-y-1/2">
            {isValidating && <Loader2 className="h-4 w-4 animate-spin text-muted-foreground" />}
            {!isValidating && isValid && <CheckCircle className="h-4 w-4 text-success" />}
            {!isValidating && status === 'invalid' && <XCircle className="h-4 w-4 text-destructive" />}
          </div>
        </div>

        {validationConfig.showValidateButton && (
          <Button
            variant={isValid ? 'default' : 'outline'}
            disabled={!value?.trim() || isValidating}
            onClick={handleValidate}
          >
            {isValidating && <Loader2 className="h-4 w-4 animate-spin" />}
            {isValidating ? 'Validating' : isValid ? 'Valid' : 'Validate'}
          </Button>
        )}

        {hasStoredKey && (
          <TooltipProvider>
            <Tooltip>
              <TooltipTrigger asChild>
                <Button variant="ghost" size="icon" onClick={handleClear}>
                  <Trash2 className="h-4 w-4" />
                </Button>
              </TooltipTrigger>
              <TooltipContent>Clear stored API key</TooltipContent>
            </Tooltip>
          </TooltipProvider>
        )}
      </div>

      {hasStoredKey && isValid && (
        <Badge variant="success" className="w-fit gap-1">
          <CheckCircle className="h-3 w-3" />
          Validated
        </Badge>
      )}
    </div>
  );
};

export default APIKeyValidator;
