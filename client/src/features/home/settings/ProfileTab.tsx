/**
 * Settings > Profile (design handoff): how employees address the owner and
 * what they should know, plus appearance and the behaviour switches.
 * Every hired employee reads these into its instructions, so the server
 * trims and bounds them again on save.
 */

import { useEffect, useRef, useState } from 'react';
import { useForm, useWatch } from 'react-hook-form';
import { zodResolver } from '@hookform/resolvers/zod';
import { Button } from '@/components/ui/button';
import { Form, FormControl, FormField, FormItem, FormLabel, FormMessage } from '@/components/ui/form';
import { Input } from '@/components/ui/input';
import { Switch } from '@/components/ui/switch';
import { Textarea } from '@/components/ui/textarea';
import { ToggleGroup, ToggleGroupItem } from '@/components/ui/toggle-group';
import { AVAILABLE_THEMES, THEME_LABEL, useTheme } from '@/contexts/ThemeContext';
import { animate } from '@/lib/motion';
import {
  browserTimezone,
  profileFromSettings,
  profileSchema,
  useOwnerSettings,
  useSaveProfile,
  type ProfileForm,
} from '../data/profile';
import { SPIKE, spikeOrb } from '../orb/orb';
import { Avatar } from '../ui/primitives';
import { pillToast } from '../ui/pillToast';
import { SettingRow } from './SettingRow';

// The fields sit on the app surface at the design's size (the primitives
// would tint them in dark and shrink the text to 13px from md up).
const FIELD_INPUT = 'h-9.5 rounded-lg bg-bg-app px-3 text-base font-normal md:text-base dark:bg-bg-app';

export function ProfileTab({ onDone }: { onDone: () => void }) {
  const { data: settings } = useOwnerSettings();
  const { saveProfile, isPending } = useSaveProfile();
  const { theme, setTheme } = useTheme();
  const [saved, setSaved] = useState(false);
  const saveRef = useRef<HTMLButtonElement>(null);
  const revealFrom = useRef<{ x: number; y: number } | undefined>(undefined);
  const zone = browserTimezone() ?? String(settings?.profile_timezone ?? '');

  const form = useForm<ProfileForm>({
    resolver: zodResolver(profileSchema),
    defaultValues: profileFromSettings(settings),
    mode: 'onSubmit',
  });

  // Fill the form once the settings row arrives, without clobbering edits.
  useEffect(() => {
    if (settings && !form.formState.isDirty) form.reset(profileFromSettings(settings));
  }, [settings, form]);

  const fullName = useWatch({ control: form.control, name: 'profile_full_name' });
  const calledAs = (useWatch({ control: form.control, name: 'profile_call_name' }) || fullName || '').trim();
  // Read while rendering, so the form keeps track of it.
  const { dirtyFields } = form.formState;

  const onSubmit = async (values: ProfileForm) => {
    // Only what was changed here: Dev's Settings panel shows Generated UI
    // too, and a value saved there meanwhile must not be written back.
    const changed = Object.fromEntries(Object.entries(values).filter(([name]) => dirtyFields[name as keyof ProfileForm]));
    try {
      await saveProfile(changed);
      form.reset(values);
      setSaved(true);
      animate(saveRef.current, [{ transform: 'scale(1)' }, { transform: 'scale(1.06)', offset: 0.4 }, { transform: 'scale(1)' }], {
        duration: 380,
        fill: 'none',
      });
      pillToast('Profile saved');
      spikeOrb(SPIKE.profileSaved);
    } catch (error) {
      pillToast(error instanceof Error ? error.message : "Couldn't save your profile", { tone: 'error' });
    }
  };

  return (
    <Form {...form}>
      <form
        onSubmit={form.handleSubmit(onSubmit)}
        noValidate
        className="flex max-w-160 flex-col gap-5.5 px-8 pt-7 pb-8"
        onChange={() => setSaved(false)}
      >
        <div data-stagger className="flex flex-col gap-1">
          <h2 className="text-lg font-semibold text-fg-default">Profile</h2>
          <p className="text-sm text-fg-muted">How your agents address you, and what they should know before they start.</p>
        </div>

        <div data-stagger className="flex items-center gap-4 rounded-card border border-border-default bg-bg-elevated p-4">
          <Avatar name={fullName || 'You'} colorRole="agent" size="lg" plain />
          <div className="flex min-w-0 flex-col gap-0.75">
            <span className="truncate text-lead font-semibold text-fg-default">{fullName || 'Your name'}</span>
            <span className="truncate font-mono text-xs text-fg-faint">
              {calledAs ? `Employees will call you ${calledAs}` : 'Add your name so employees know you'}
            </span>
          </div>
        </div>

        <div data-stagger className="grid grid-cols-[repeat(auto-fit,minmax(220px,1fr))] gap-3.5">
          <FormField
            control={form.control}
            name="profile_full_name"
            render={({ field }) => (
              <FormItem>
                <FormLabel className="text-xs text-fg-muted">Full name</FormLabel>
                <FormControl>
                  <Input placeholder="Alex Rivera" autoComplete="name" className={FIELD_INPUT} {...field} />
                </FormControl>
                <FormMessage />
              </FormItem>
            )}
          />
          <FormField
            control={form.control}
            name="profile_call_name"
            render={({ field }) => (
              <FormItem>
                <FormLabel className="text-xs text-fg-muted">What should agents call you?</FormLabel>
                <FormControl>
                  <Input placeholder="Alex" autoComplete="given-name" className={FIELD_INPUT} {...field} />
                </FormControl>
                <FormMessage />
              </FormItem>
            )}
          />
          <FormField
            control={form.control}
            name="profile_role"
            render={({ field }) => (
              <FormItem>
                <FormLabel className="text-xs text-fg-muted">Role</FormLabel>
                <FormControl>
                  <Input placeholder="Founder, ops lead, freelancer…" autoComplete="organization-title" className={FIELD_INPUT} {...field} />
                </FormControl>
                <FormMessage />
              </FormItem>
            )}
          />
          <FormItem>
            <FormLabel className="text-xs text-fg-muted" htmlFor="profile-timezone">
              Timezone
            </FormLabel>
            <Input id="profile-timezone" value={zone} readOnly className={`${FIELD_INPUT} font-mono text-sm text-fg-muted md:text-sm`} />
          </FormItem>
        </div>

        <FormField
          control={form.control}
          name="profile_preferences"
          render={({ field }) => (
            <FormItem data-stagger>
              <FormLabel className="flex flex-col items-start gap-0.5 text-xs text-fg-muted">
                Personal preferences
                <span className="font-normal text-fg-faint">Applied to every chat and agent run.</span>
              </FormLabel>
              <FormControl>
                <Textarea
                  rows={4}
                  placeholder="Keep replies short. Draft emails in my voice, never send without asking."
                  className="min-h-24 resize-y bg-bg-app px-3 py-2.5 text-base md:text-base dark:bg-bg-app"
                  {...field}
                />
              </FormControl>
              <FormMessage />
            </FormItem>
          )}
        />

        <div data-stagger className="flex flex-col">
          <SettingRow title="Appearance" detail="Choose a theme.">
            <ToggleGroup
              type="single"
              variant="segmented"
              aria-label="Appearance"
              value={theme}
              // The new theme grows from where it was clicked.
              onPointerDown={(event) => {
                revealFrom.current = { x: event.clientX, y: event.clientY };
              }}
              onValueChange={(next) => {
                const chosen = AVAILABLE_THEMES.find((name) => name === next);
                if (chosen) setTheme(chosen, { reveal: true, origin: revealFrom.current });
                revealFrom.current = undefined;
              }}
            >
              {AVAILABLE_THEMES.map((name) => (
                <ToggleGroupItem key={name} value={name} className="px-3.5 text-xs">
                  {THEME_LABEL[name]}
                </ToggleGroupItem>
              ))}
            </ToggleGroup>
          </SettingRow>
          <FormField
            control={form.control}
            name="memory_across_chats"
            render={({ field }) => (
              <SettingRow title="Memory across chats" detail="Agents remember what you share and reuse it later.">
                <Switch size="md" tone="ink" aria-label="Memory across chats" checked={field.value} onCheckedChange={field.onChange} />
              </SettingRow>
            )}
          />
          <FormField
            control={form.control}
            name="prefer_local_ai"
            render={({ field }) => (
              <SettingRow title="Keep everything on this computer" detail="Use AI that runs on this computer whenever possible.">
                <Switch
                  size="md"
                  tone="ink"
                  aria-label="Keep everything on this computer"
                  checked={field.value}
                  onCheckedChange={field.onChange}
                />
              </SettingRow>
            )}
          />
          <FormField
            control={form.control}
            name="generated_ui"
            render={({ field }) => (
              <SettingRow
                title="Generated UI"
                detail="Employees can add forms, buttons and suggested questions to their replies, and hiring shows a setup screen made for each job. Uses more AI."
                last
              >
                <Switch size="md" tone="ink" aria-label="Generated UI" checked={field.value} onCheckedChange={field.onChange} />
              </SettingRow>
            )}
          />
        </div>

        <div data-stagger className="flex justify-end gap-2">
          <Button type="button" variant="quiet" className="h-8.5 border-border-default px-3.5 font-semibold text-fg-default" onClick={onDone}>
            Cancel
          </Button>
          <Button ref={saveRef} type="submit" variant="invert" disabled={isPending} className="h-8.5 px-4 font-semibold">
            {isPending ? 'Saving…' : saved ? 'Saved' : 'Save changes'}
          </Button>
        </div>
      </form>
    </Form>
  );
}

export default ProfileTab;
