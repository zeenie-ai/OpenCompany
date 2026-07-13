/**
 * Canonical OpenCompany browser-storage keys and their pre-rebrand aliases.
 *
 * Keep every brand-key migration in one place so feature modules cannot
 * accidentally diverge on spelling or drop a returning user's preferences.
 */
export const BRAND_STORAGE_KEYS = {
  theme: {
    canonical: 'opencompany-theme',
    legacy: 'machinaos-theme',
  },
  sound: {
    canonical: 'opencompany-sound',
    legacy: 'machinaos-sound',
  },
  queryCache: {
    canonical: 'opencompany-query-cache',
    legacy: 'machina-query-cache',
  },
  nodeSpecRevision: {
    canonical: 'opencompany-nodespec-revision',
    legacy: 'machina-nodespec-revision',
  },
} as const;

export interface BrandedStorageKeyPair {
  canonical: string;
  legacy: string;
}

type StorageReader = Pick<Storage, 'getItem' | 'setItem' | 'removeItem'>;

/**
 * Read the canonical value, falling back to a legacy branded key once.
 *
 * When a legacy value exists it is copied before the old key is removed, so
 * an interrupted migration never loses user state. If writes are unavailable
 * (for example, storage is browser-policy blocked), the legacy value still
 * works for the current session and migration can be retried next launch.
 */
export function readAndMigrateStorageValue(
  storage: StorageReader | null | undefined,
  keys: BrandedStorageKeyPair,
): string | null {
  if (!storage) return null;

  try {
    const canonicalValue = storage.getItem(keys.canonical);
    if (canonicalValue !== null) {
      try {
        storage.removeItem(keys.legacy);
      } catch {
        // Canonical data already won; stale-key cleanup is best-effort.
      }
      return canonicalValue;
    }

    const legacyValue = storage.getItem(keys.legacy);
    if (legacyValue === null) return null;

    try {
      storage.setItem(keys.canonical, legacyValue);
      storage.removeItem(keys.legacy);
    } catch {
      // Return the legacy value even when persistent migration is blocked.
    }

    return legacyValue;
  } catch {
    return null;
  }
}
