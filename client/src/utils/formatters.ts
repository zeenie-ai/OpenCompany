// Common utility functions for formatting and clipboard operations
import { toast } from 'sonner';

/**
 * Copy text to clipboard with error handling
 */
export const copyToClipboard = async (data: any, successMessage?: string): Promise<boolean> => {
  try {
    const text = typeof data === 'string' ? data : JSON.stringify(data, null, 2);
    await navigator.clipboard.writeText(text);
    if (successMessage) {
      toast.success(successMessage);
    }
    return true;
  } catch (error) {
    console.error('Failed to copy to clipboard:', error);
    return false;
  }
};

/**
 * Format object as JSON string
 */
export const formatJson = (obj: any, compact: boolean = false): string => {
  return JSON.stringify(obj, null, compact ? 0 : 2);
};

/**
 * Parse a string that is wholly a JSON object/array; null otherwise.
 * Stdlib JSON.parse behind a cheap shape check — used by output
 * surfaces to route JSON-looking CLI/stdout strings to the tree
 * viewer instead of markdown/plain text.
 */
export const tryParseJson = (s: string): object | null => {
  const t = s.trim();
  const looksJson = (t.startsWith('{') && t.endsWith('}')) || (t.startsWith('[') && t.endsWith(']'));
  if (!looksJson) return null;
  try {
    const v = JSON.parse(t);
    return typeof v === 'object' && v !== null ? v : null;
  } catch {
    return null;
  }
};

/**
 * Format timestamp to locale string
 */
export const formatTimestamp = (timestamp: string | number | Date): string => {
  return new Date(timestamp).toLocaleString();
};