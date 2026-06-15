import React from 'react';
import { Key, ExternalLink } from 'lucide-react';

import { Button } from '@/components/ui/button';
import { Alert, AlertDescription } from '@/components/ui/alert';
import {
  OpenAIIcon, ClaudeIcon, GeminiIcon, GroqIcon, OpenRouterIcon, CerebrasIcon,
} from '../../icons/AIProviderIcons';

interface ApiKeyStepProps {
  onOpenCredentials: () => void;
}

const providers = [
  { name: 'OpenAI', icon: <OpenAIIcon />, desc: 'GPT-4o, o3, o4 models', url: 'platform.openai.com' },
  { name: 'Anthropic', icon: <ClaudeIcon />, desc: 'Claude Opus, Sonnet models', url: 'console.anthropic.com' },
  { name: 'Google', icon: <GeminiIcon />, desc: 'Gemini Pro, Flash models', url: 'aistudio.google.com' },
  { name: 'Groq', icon: <GroqIcon />, desc: 'Ultra-fast Llama, Qwen', url: 'console.groq.com' },
  { name: 'OpenRouter', icon: <OpenRouterIcon />, desc: 'Access 200+ models', url: 'openrouter.ai' },
  { name: 'Cerebras', icon: <CerebrasIcon />, desc: 'Fast inference on custom HW', url: 'cloud.cerebras.ai' },
];

const ApiKeyStep: React.FC<ApiKeyStepProps> = ({ onOpenCredentials }) => {
  return (
    <div className="py-1">
      <div className="mb-4 text-center">
        <h4 className="m-0 mb-1 flex items-center justify-center gap-2 text-lg font-semibold">
          <Key className="h-4 w-4 text-warning" />
          API Key Setup
        </h4>
        <p className="text-xs text-muted-foreground">
          Configure at least one AI provider to use AI agents
        </p>
      </div>

      <div className="flex w-full flex-col gap-2">
        {providers.map((p) => (
          <div
            key={p.name}
            className="flex items-center gap-2.5 rounded-md border border-border bg-muted/50 px-3 py-2"
          >
            <div className="flex h-6 w-6 shrink-0 items-center justify-center">{p.icon}</div>
            <div className="min-w-0 flex-1">
              <div className="block text-sm font-semibold">{p.name}</div>
              <div className="text-xs text-muted-foreground">{p.desc}</div>
            </div>
            <div className="flex items-center gap-1 text-[10px] text-muted-foreground">
              <ExternalLink className="h-3 w-3" /> {p.url}
            </div>
          </div>
        ))}
      </div>

      <div className="mt-5 text-center">
        <Button onClick={onOpenCredentials} className="gap-2">
          <Key className="h-4 w-4" />
          Open Credentials
        </Button>
      </div>

      <Alert variant="info" className="mt-4">
        <AlertDescription className="text-xs">
          You can always change API keys later from the toolbar credentials button.
        </AlertDescription>
      </Alert>
    </div>
  );
};

export default ApiKeyStep;
