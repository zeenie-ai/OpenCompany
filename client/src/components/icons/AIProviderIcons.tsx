/* eslint-disable react-refresh/only-export-components -- icon component file mixes component + helper exports. */
// AI Provider Icons - Using @lobehub/icons for official brand logos.
// Deep imports keep the package's antd-using `Editor`/`Dashboard`/`Provider*`
// feature modules out of the bundle (their index re-export would otherwise
// drag `antd` -> `@ant-design/icons` -> `@ant-design/colors` into rollup).
import React from 'react';
import OpenAI from '@lobehub/icons/es/OpenAI';
import Claude from '@lobehub/icons/es/Claude';
import Gemini from '@lobehub/icons/es/Gemini';
import Groq from '@lobehub/icons/es/Groq';
import OpenRouter from '@lobehub/icons/es/OpenRouter';
import Cerebras from '@lobehub/icons/es/Cerebras';
import DeepSeek from '@lobehub/icons/es/DeepSeek';
import Kimi from '@lobehub/icons/es/Kimi';
import Mistral from '@lobehub/icons/es/Mistral';
import Ollama from '@lobehub/icons/es/Ollama';
import LmStudio from '@lobehub/icons/es/LmStudio';
import { dracula, solarized } from '../../styles/theme';

// Icon size constant for consistency
const ICON_SIZE = 28;

// Export icon components with consistent sizing
// Each provider has different available variants - use Avatar for consistency
export const OpenAIIcon: React.FC<{ size?: number }> = ({ size = ICON_SIZE }) => (
  <OpenAI.Avatar size={size} />
);

export const ClaudeIcon: React.FC<{ size?: number }> = ({ size = ICON_SIZE }) => (
  <Claude.Color size={size} />
);

export const GeminiIcon: React.FC<{ size?: number }> = ({ size = ICON_SIZE }) => (
  <Gemini.Color size={size} />
);

// Groq uses Avatar variant (no Color variant available)
export const GroqIcon: React.FC<{ size?: number }> = ({ size = ICON_SIZE }) => (
  <Groq.Avatar size={size} />
);

// OpenRouter uses Avatar variant (no Color variant available)
export const OpenRouterIcon: React.FC<{ size?: number }> = ({ size = ICON_SIZE }) => (
  <OpenRouter.Avatar size={size} />
);

// Cerebras uses Color variant
export const CerebrasIcon: React.FC<{ size?: number }> = ({ size = ICON_SIZE }) => (
  <Cerebras.Color size={size} />
);

export const DeepSeekIcon: React.FC<{ size?: number }> = ({ size = ICON_SIZE }) => (
  <DeepSeek.Color size={size} />
);

export const KimiIcon: React.FC<{ size?: number }> = ({ size = ICON_SIZE }) => (
  <Kimi.Color size={size} />
);

export const MistralIcon: React.FC<{ size?: number }> = ({ size = ICON_SIZE }) => (
  <Mistral.Color size={size} />
);

// Local LLM servers — Ollama exposes a Color variant; LmStudio's lobehub
// entry only ships an Avatar so we use that for parity.
export const OllamaIcon: React.FC<{ size?: number }> = ({ size = ICON_SIZE }) => (
  <Ollama.Avatar size={size} />
);

export const LmStudioIcon: React.FC<{ size?: number }> = ({ size = ICON_SIZE }) => (
  <LmStudio.Avatar size={size} />
);

// Map provider IDs to their icon components
export const AI_PROVIDER_ICONS: Record<string, React.FC<{ size?: number }>> = {
  openai: OpenAIIcon,
  anthropic: ClaudeIcon,
  gemini: GeminiIcon,
  groq: GroqIcon,
  openrouter: OpenRouterIcon,
  cerebras: CerebrasIcon,
  deepseek: DeepSeekIcon,
  kimi: KimiIcon,
  mistral: MistralIcon,
  ollama: OllamaIcon,
  lmstudio: LmStudioIcon,
};

// Centralized provider metadata (icon ref, theme color, display label).
// `iconRef` uses the prefix-dispatch contract resolved by `<NodeIcon>` —
// `lobehub:<brand>` picks the package's `.Color` (or `.Avatar`)
// component. `Icon` stays as a pre-built FC for the remaining direct
// consumers.
export const AI_PROVIDER_META: Record<string, { iconRef: string; Icon: React.FC<{ size?: number }>; color: string; label: string }> = {
  openai:     { iconRef: 'lobehub:OpenAI',     Icon: OpenAIIcon,     color: dracula.green,    label: 'OpenAI' },
  anthropic:  { iconRef: 'lobehub:Claude',     Icon: ClaudeIcon,     color: dracula.orange,   label: 'Anthropic' },
  gemini:     { iconRef: 'lobehub:Gemini',     Icon: GeminiIcon,     color: solarized.blue,   label: 'Gemini' },
  groq:       { iconRef: 'lobehub:Groq',       Icon: GroqIcon,       color: dracula.red,      label: 'Groq' },
  cerebras:   { iconRef: 'lobehub:Cerebras',   Icon: CerebrasIcon,   color: dracula.orange,   label: 'Cerebras' },
  openrouter: { iconRef: 'lobehub:OpenRouter', Icon: OpenRouterIcon, color: solarized.violet, label: 'OpenRouter' },
  deepseek:   { iconRef: 'lobehub:DeepSeek',   Icon: DeepSeekIcon,   color: dracula.cyan,     label: 'DeepSeek' },
  kimi:       { iconRef: 'lobehub:Kimi',       Icon: KimiIcon,       color: dracula.purple,   label: 'Kimi' },
  mistral:    { iconRef: 'lobehub:Mistral',    Icon: MistralIcon,    color: dracula.pink,     label: 'Mistral' },
  ollama:     { iconRef: 'lobehub:Ollama',     Icon: OllamaIcon,     color: dracula.foreground, label: 'Ollama' },
  lmstudio:   { iconRef: 'lobehub:LmStudio',   Icon: LmStudioIcon,   color: solarized.cyan,   label: 'LM Studio' },
};

// Get icon component by provider ID
export const getAIProviderIcon = (providerId: string): React.FC<{ size?: number }> | null => {
  return AI_PROVIDER_ICONS[providerId] || null;
};
