/**
 * Starter jobs for the hire composer (design handoff "Template chips"): a
 * label, the role colour it wears, and the plain-English job it sends.
 */

import type { ColorRole } from '../data/schemas';

export interface HireTemplate {
  label: string;
  role: ColorRole;
  job: string;
}

export const HIRE_TEMPLATES: readonly HireTemplate[] = [
  {
    label: 'Receptionist',
    role: 'agent',
    job: 'A receptionist who answers customer messages on WhatsApp, handles common questions and books appointments in my calendar.',
  },
  {
    label: 'Inbox assistant',
    role: 'model',
    job: 'An inbox assistant who sorts my email every morning, flags anything urgent and drafts replies for me to approve.',
  },
  {
    label: 'Bookkeeper',
    role: 'workflow',
    job: 'A bookkeeper who tracks payments in Stripe, politely chases unpaid invoices and sends me a weekly summary.',
  },
  {
    label: 'Social media helper',
    role: 'trigger',
    job: 'A social media helper who drafts three posts a week about my business and sends them to me for approval.',
  },
];
