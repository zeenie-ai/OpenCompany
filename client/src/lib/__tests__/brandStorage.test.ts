import { beforeEach, describe, expect, it } from 'vitest';
import { BRAND_STORAGE_KEYS, readAndMigrateStorageValue } from '../brandStorage';

beforeEach(() => {
  localStorage.clear();
});

describe('OpenCompany browser-storage migration', () => {
  it('moves every pre-rebrand key to its canonical OpenCompany key', () => {
    for (const keys of Object.values(BRAND_STORAGE_KEYS)) {
      const value = `saved:${keys.legacy}`;
      localStorage.setItem(keys.legacy, value);

      expect(readAndMigrateStorageValue(localStorage, keys)).toBe(value);
      expect(localStorage.getItem(keys.canonical)).toBe(value);
      expect(localStorage.getItem(keys.legacy)).toBeNull();
    }
  });

  it('keeps a canonical value when both keys exist and removes the stale alias', () => {
    const keys = BRAND_STORAGE_KEYS.theme;
    localStorage.setItem(keys.canonical, 'renaissance');
    localStorage.setItem(keys.legacy, 'dark');

    expect(readAndMigrateStorageValue(localStorage, keys)).toBe('renaissance');
    expect(localStorage.getItem(keys.canonical)).toBe('renaissance');
    expect(localStorage.getItem(keys.legacy)).toBeNull();
  });

  it('returns null when storage is unavailable', () => {
    expect(readAndMigrateStorageValue(undefined, BRAND_STORAGE_KEYS.sound)).toBeNull();
  });
});
