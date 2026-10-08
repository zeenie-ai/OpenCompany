import { z } from 'zod';

/**
 * Validation schema for the login / register form, in the words of the
 * onboarding handoff (R5).
 *
 * Mirrors the shape of `credentials/panels/schemas/email.ts` so both forms
 * are validated the same way. Email format is checked here because the
 * server uses `EmailStr`, so a malformed address would come back as a 422
 * whose `detail` is a list of objects rather than a string.
 */
export function createAuthFormSchema(isRegistering: boolean) {
  return z.object({
    email: z
      .string()
      .min(1, 'Enter your email.')
      .pipe(z.email('That doesn’t look like an email address.')),
    password: isRegistering
      ? z.string().min(1, 'Enter your password.').min(8, 'Use at least 8 characters.')
      : z.string().min(1, 'Enter your password.'),
    // Required only when registering. The 100-character ceiling mirrors the
    // `max_length=100` column on `User.display_name`; SQLite truncates
    // silently rather than raising, so an over-long name would be accepted
    // and then quietly altered.
    displayName: isRegistering
      ? z.string().trim().min(1, 'Enter your name.').max(100, 'Use 100 characters or fewer.')
      : z.string().optional(),
  });
}

export type AuthFormValues = z.infer<ReturnType<typeof createAuthFormSchema>>;
