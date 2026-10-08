/**
 * Sign in and register (onboarding handoff R5), inside the gate's
 * ConnectScreen: the orb above a card that rises in, and shakes when a
 * field is wrong or the server refuses. "Welcome back" or "Create your
 * account", the fields, Sign in / Create account, and the way to the other
 * mode while registration is open. Once signed in, the card gives way to
 * SignedIn, which ends the sign-in (`finishSignIn`) when the app can show.
 *
 * Built on react-hook-form + zod through the shadcn `Form` primitives, the
 * same composition `EmailPanel` uses: `FormControl` emits `aria-invalid`
 * and wires `aria-describedby` to the matching `FormMessage`.
 *
 * The only failure shown here is what the server said (`submitError`: a
 * wrong password, a taken email, a rate limit, or no answer at all). A
 * server that can't be reached at all is the gate's Connecting screen, not
 * this card's. The orb follows the form: livelier while a field has focus,
 * more with text, most while the request is out.
 */

import React, { useEffect, useLayoutEffect, useMemo, useRef, useState } from 'react';
import { useForm, useWatch } from 'react-hook-form';
import { zodResolver } from '@hookform/resolvers/zod';
import { ArrowRight, CircleAlert, Eye, EyeOff } from 'lucide-react';

import { useAuth } from '../../contexts/AuthContext';
import { Alert, AlertDescription } from '@/components/ui/alert';
import { Button } from '@/components/ui/button';
import { Form, FormControl, FormField, FormItem, FormLabel, FormMessage } from '@/components/ui/form';
import { Input } from '@/components/ui/input';
import { RingSpinner } from '@/components/ui/ring-spinner';
import { ENERGY, setEnergyTarget } from '@/features/home/orb/orb';
import { OrbSlot } from '@/features/home/orb/OrbSlot';
import { rise, shake } from '@/lib/motion';
import { createAuthFormSchema, type AuthFormValues } from './schemas/login';
import { SignedIn } from './SignedIn';

const FIELD = 'h-10 rounded-row bg-bg-app px-3 text-base md:text-base dark:bg-bg-app';

const LoginPage: React.FC = () => {
  const { login, register, canRegister, submitError, isSubmitting, resetAuthErrors, isAuthenticated, user } = useAuth();

  const [isRegistering, setIsRegistering] = useState(false);
  const [showPassword, setShowPassword] = useState(false);
  const [focused, setFocused] = useState(false);
  const cardRef = useRef<HTMLDivElement>(null);

  const schema = useMemo(() => createAuthFormSchema(isRegistering), [isRegistering]);

  const form = useForm<AuthFormValues>({
    resolver: zodResolver(schema),
    defaultValues: { email: '', password: '', displayName: '' },
    mode: 'onSubmit',
  });
  const [email, password, displayName] = useWatch({
    control: form.control,
    name: ['email', 'password', 'displayName'],
  });
  const typed = Boolean(email || password || displayName);

  useLayoutEffect(() => {
    rise(cardRef.current);
  }, []);

  useEffect(() => {
    setEnergyTarget(isSubmitting ? ENERGY.generating : focused ? (typed ? ENERGY.typing : ENERGY.focus) : ENERGY.idle);
  }, [isSubmitting, focused, typed]);
  useEffect(() => () => setEnergyTarget(ENERGY.idle), []);

  const onSubmit = async (submitted: AuthFormValues) => {
    resetAuthErrors();
    const ok = isRegistering
      ? await register(submitted.email, submitted.password, submitted.displayName ?? '')
      : await login(submitted.email, submitted.password);
    // The refusal itself is on `submitError`.
    if (!ok) shake(cardRef.current);
  };

  const toggleMode = () => {
    setIsRegistering((prev) => !prev);
    resetAuthErrors();
    form.clearErrors();
  };

  return (
    <div className="flex w-full max-w-100 flex-col items-center gap-4.5">
      <OrbSlot size="login" />
      {isAuthenticated ? (
        <SignedIn created={isRegistering} name={user?.display_name ?? ''} />
      ) : (
        <div
          ref={cardRef}
          className="flex w-full flex-col gap-5 rounded-draft border border-border-default bg-bg-panel p-7 shadow-float"
        >
          <h1 className="m-0 text-center text-xl leading-tight font-semibold tracking-hero text-fg-default">
            {isRegistering ? 'Create your account' : 'Welcome back'}
          </h1>

          {submitError && (
            <Alert
              variant="destructive"
              aria-live="assertive"
              className="rounded-row border-danger-border bg-danger-soft px-3 py-2.5 text-sm"
            >
              <CircleAlert aria-hidden className="size-3.75" />
              <AlertDescription>{submitError}</AlertDescription>
            </Alert>
          )}

          <Form {...form}>
            {/* noValidate: zod owns validation. Otherwise the browser's own
                constraint check on type="email" silently blocks submit and
                shows a native bubble, which neither matches FormMessage
                styling nor respects the schema's rules. */}
            <form
              onSubmit={form.handleSubmit(onSubmit, () => shake(cardRef.current))}
              onFocus={() => setFocused(true)}
              onBlur={() => setFocused(false)}
              className="flex flex-col gap-3.5"
              noValidate
            >
              {isRegistering && (
                <FormField
                  control={form.control}
                  name="displayName"
                  render={({ field }) => (
                    <FormItem className="gap-1.5">
                      <FormLabel className="text-sm font-medium">Your name</FormLabel>
                      <FormControl>
                        <Input
                          placeholder="Jordan Lee"
                          autoComplete="name"
                          disabled={isSubmitting}
                          className={FIELD}
                          {...field}
                          value={field.value ?? ''}
                        />
                      </FormControl>
                      <FormMessage className="text-meta" />
                    </FormItem>
                  )}
                />
              )}

              <FormField
                control={form.control}
                name="email"
                render={({ field }) => (
                  <FormItem className="gap-1.5">
                    <FormLabel className="text-sm font-medium">Email</FormLabel>
                    <FormControl>
                      <Input
                        type="email"
                        placeholder="you@example.com"
                        autoComplete="email"
                        disabled={isSubmitting}
                        className={FIELD}
                        {...field}
                      />
                    </FormControl>
                    <FormMessage className="text-meta" />
                  </FormItem>
                )}
              />

              <FormField
                control={form.control}
                name="password"
                render={({ field }) => (
                  <FormItem className="gap-1.5">
                    <FormLabel className="text-sm font-medium">Password</FormLabel>
                    <div className="relative">
                      <FormControl>
                        <Input
                          type={showPassword ? 'text' : 'password'}
                          placeholder={isRegistering ? 'At least 8 characters' : 'Your password'}
                          autoComplete={isRegistering ? 'new-password' : 'current-password'}
                          disabled={isSubmitting}
                          className={`${FIELD} pr-10`}
                          {...field}
                        />
                      </FormControl>
                      <Button
                        type="button"
                        variant="quiet"
                        size="icon-sm"
                        aria-label={showPassword ? 'Hide password' : 'Show password'}
                        aria-pressed={showPassword}
                        onClick={() => setShowPassword((on) => !on)}
                        className="absolute top-1/2 right-2 -translate-y-1/2 rounded-lg"
                      >
                        {showPassword ? <EyeOff aria-hidden /> : <Eye aria-hidden />}
                      </Button>
                    </div>
                    <FormMessage className="text-meta" />
                  </FormItem>
                )}
              />

              <Button
                type="submit"
                variant="invert"
                disabled={isSubmitting}
                className="mt-1 h-10.5 w-full gap-2 rounded-row text-base font-semibold disabled:bg-fg-default disabled:text-bg-app disabled:opacity-70"
              >
                {isSubmitting ? (
                  <>
                    <RingSpinner className="size-3.5 border-fg-faint border-t-bg-app" />
                    {isRegistering ? 'Creating your account…' : 'Signing in…'}
                  </>
                ) : (
                  <>
                    {isRegistering ? 'Create account' : 'Sign in'}
                    <ArrowRight aria-hidden strokeWidth={2.25} />
                  </>
                )}
              </Button>
            </form>
          </Form>

          {canRegister && (
            <p className="m-0 border-t border-border-default pt-4 text-center text-row text-fg-muted">
              {isRegistering ? 'Already have an account?' : 'Don’t have an account?'}{' '}
              <Button
                type="button"
                variant="link"
                onClick={toggleMode}
                disabled={isSubmitting}
                className="h-auto p-0 text-row font-semibold text-fg-default"
              >
                {isRegistering ? 'Sign in' : 'Register'}
              </Button>
            </p>
          )}
        </div>
      )}
    </div>
  );
};

export default LoginPage;
