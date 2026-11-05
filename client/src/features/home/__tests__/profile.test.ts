import { describe, expect, it } from 'vitest';
import { callName, profileFromSettings, profileSchema } from '../data/profile';

describe('profile', () => {
  it('reads the settings row with the switches on by default, Generated UI off', () => {
    expect(profileFromSettings(undefined)).toEqual({
      profile_full_name: '',
      profile_call_name: '',
      profile_role: '',
      profile_preferences: '',
      memory_across_chats: true,
      prefer_local_ai: true,
      generated_ui: false,
    });
    expect(profileFromSettings({ prefer_local_ai: false, profile_full_name: 'Alex Rivera' })).toMatchObject({
      profile_full_name: 'Alex Rivera',
      prefer_local_ai: false,
    });
  });

  it('turns Generated UI on only for a real true', () => {
    expect(profileFromSettings({ generated_ui: true }).generated_ui).toBe(true);
    expect(profileFromSettings({ generated_ui: 'true' }).generated_ui).toBe(false);
  });

  it('calls the owner by the call name, else the first name', () => {
    expect(callName({ profile_call_name: ' Al ', profile_full_name: 'Alex Rivera' })).toBe('Al');
    expect(callName({ profile_full_name: 'Alex Rivera' })).toBe('Alex');
    expect(callName(undefined)).toBe('');
  });

  it('bounds the fields as the server does', () => {
    const base = profileFromSettings(undefined);
    expect(profileSchema.safeParse({ ...base, profile_call_name: 'x'.repeat(61) }).success).toBe(false);
    expect(profileSchema.safeParse({ ...base, profile_preferences: 'x'.repeat(2001) }).success).toBe(false);
    expect(profileSchema.parse({ ...base, profile_full_name: '  Alex  ' }).profile_full_name).toBe('Alex');
  });
});
