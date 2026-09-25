/**
 * Settings > Profile (design handoff): how employees address the owner and
 * what they should know, plus appearance and the two behaviour switches.
 * Every hired employee reads these into its instructions, so the server
 * trims and bounds them again on save.
 */

import { useEffect, useRef, useState, type ReactNode } from 'react';
import { useForm, useWatch } from 'react-hook-form';
import { zodResolver } from '@hookform/resolvers/zod';
import { ActionButton } from '@/components/ui/action-button';
import { Button } from '@/components/ui/button';
import { Form, FormControl, FormField, FormItem, FormLabel, FormMessage } from '@/components/ui/form';
import { Input } from '@/components/ui/input';
import { Switch } from '@/components/ui/switch';
import { Textarea } from '@/components/ui/textarea';
import { ToggleGroup, ToggleGroupItem } from '@/components/ui/toggle-group';
import { isDarkTheme, useTheme } from '@/contexts/ThemeContext';
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

// The fields sit on the app surface at the design's size (the primitives
// would tint them in dark and shrink the text to 13px from md up).
const FIELD_INPUT = 'h-9.5 rounded-lg bg-bg-app px-3 text-base font-normal md:text-base dark:bg-bg-app';

function SettingRow({
  title,
  detail,
  children,
  last = false,
}: {
  title: string;
  detail: string;
  children: ReactNode;
  last?: boolean;
}) {
  return (
    <div className={`flex items-center gap-4 border-t border-border-default py-3.5 ${last ? 'border-b' : ''}`}>
      <div className="flex flex-1 flex-col gap-0.75">
        <span className="text-base font-medium text-fg-default">{title}</span>
        <span className="text-meta text-fg-muted">{detail}</span>
      </div>
      {children}
    </div>
  );
}

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

  const onSubmit = async (values: ProfileForm) => {
    try {
      await saveProfile(values);
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
          <Avatar name={fullName || 'You'} colorRole="agent" size="lg" className="shadow-[0_0_24px_var(--node-agent-fill)]" />
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
          <SettingRow title="Appearance" detail="Choose light or dark.">
            <ToggleGroup
              type="single"
              variant="segmented"
              aria-label="Appearance"
              value={isDarkTheme(theme) ? 'dark' : 'light'}
              // The new theme grows from where it was clicked.
              onPointerDown={(event) => {
                revealFrom.current = { x: event.clientX, y: event.clientY };
              }}
              onValueChange={(next) => {
                if (next === 'light' || next === 'dark') setTheme(next, { reveal: true, origin: revealFrom.current });
                revealFrom.current = undefined;
              }}
            >
              <ToggleGroupItem value="light" className="px-3.5 text-xs">
                Light
              </ToggleGroupItem>
              <ToggleGroupItem value="dark" className="px-3.5 text-xs">
                Dark
              </ToggleGroupItem>
            </ToggleGroup>
          </SettingRow>
          <FormField
            control={form.control}
            name="memory_across_chats"
            render={({ field }) => (
              <SettingRow title="Memory across chats" detail="Agents remember what you share and reuse it later.">
                <Switch size="md" tone="run" aria-label="Memory across chats" checked={field.value} onCheckedChange={field.onChange} />
              </SettingRow>
            )}
          />
          <FormField
            control={form.control}
            name="prefer_local_ai"
            render={({ field }) => (
              <SettingRow title="Keep everything on this computer" detail="Use AI that runs on this computer whenever possible." last>
                <Switch
                  size="md"
                  tone="run"
                  aria-label="Keep everything on this computer"
                  checked={field.value}
                  onCheckedChange={field.onChange}
                />
              </SettingRow>
            )}
          />
        </div>

        <div data-stagger className="flex justify-end gap-2">
          <Button type="button" variant="quiet" className="h-8.5 border-border-default px-3.5 font-semibold text-fg-default" onClick={onDone}>
            Cancel
          </Button>
          <ActionButton ref={saveRef} type="submit" intent="run" disabled={isPending} className="h-8.5 px-4">
            {isPending ? 'Saving…' : saved ? 'Saved' : 'Save changes'}
          </ActionButton>
        </div>
      </form>
    </Form>
  );
}

export default ProfileTab;
