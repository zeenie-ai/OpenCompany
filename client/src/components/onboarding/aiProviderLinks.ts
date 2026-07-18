/**
 * Featured AI providers for onboarding surfaces (wizard + Get Started
 * checklist). Only marketing hints and key-page URLs live here — display
 * name, icon, and stored state come from the live credential catalogue
 * (`useCatalogueQuery`), which is the single source of truth.
 */

export interface FeaturedAiProvider {
  /** Catalogue provider id (`ServerProviderConfig.id`). */
  id: string;
  /** One-line plain-language hint shown under the provider name. */
  hint: string;
  /** Direct link to the page where the user generates an API key. */
  keyUrl: string;
}

export const FEATURED_AI_PROVIDERS: FeaturedAiProvider[] = [
  { id: 'openai', hint: 'Runs GPT models', keyUrl: 'https://platform.openai.com/api-keys' },
  { id: 'anthropic', hint: 'Runs Claude models', keyUrl: 'https://console.anthropic.com/settings/keys' },
  { id: 'gemini', hint: 'Runs Gemini models', keyUrl: 'https://aistudio.google.com/apikey' },
];
