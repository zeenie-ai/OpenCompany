/**
 * Featured AI providers: where to get a key, and a one-line hint. The
 * provider page links "Get a key from {name}" for these. Only the hint and
 * the key-page URL live here; the name, icon and connected state come from
 * the live credential catalogue.
 */

export interface FeaturedAiProvider {
  /** Catalogue provider id (`ServerProviderConfig.id`). */
  id: string;
  /** One-line plain-language hint, used when the catalogue has no description. */
  hint: string;
  /** Direct link to the page where the user generates an API key. */
  keyUrl: string;
}

export const FEATURED_AI_PROVIDERS: FeaturedAiProvider[] = [
  { id: 'openai', hint: 'Runs GPT models', keyUrl: 'https://platform.openai.com/api-keys' },
  { id: 'anthropic', hint: 'Runs Claude models', keyUrl: 'https://console.anthropic.com/settings/keys' },
  { id: 'gemini', hint: 'Runs Gemini models', keyUrl: 'https://aistudio.google.com/apikey' },
];
