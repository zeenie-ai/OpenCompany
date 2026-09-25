/**
 * Design QA only: `?fixture=employees` in a dev build fills the team with
 * the handoff's three sample employees instead of asking the server, so
 * the sidebar and the employee card can be checked against the prototype
 * with no workflows set up. Production builds drop this module (the only
 * import sits behind `import.meta.env.DEV`).
 *
 * The rows go through the real parser, so they also exercise it.
 */

import { parseEmployees, type EmployeeSummary } from './schemas';

function control(state: string, revision: number) {
  const running = state === 'running';
  return {
    state,
    revision,
    generation: state === 'never_started' ? 0 : 1,
    active_count: running ? 1 : 0,
    in_flight_count: 0,
    queued_count: 0,
    can_start: state === 'never_started',
    can_pause: running,
    can_resume: state === 'paused',
    can_reset: state !== 'never_started',
    can_edit: !running,
  };
}

function app(app_id: string, name: string, provider_id: string, connected: boolean) {
  return { app_id, name, provider_id, icon_ref: null, connected, supported: true };
}

const ROWS = [
  {
    workflow_id: 'fixture-maya',
    name: 'Maya',
    role: 'Receptionist',
    color_role: 'agent',
    derived: false,
    status: 'working',
    task: { label: 'Now', text: 'Replying to Priya about Saturday' },
    done_today: 23,
    pending_approvals: 1,
    apps: [app('whatsapp', 'WhatsApp', 'whatsapp', true), app('google_calendar', 'Google Calendar', 'google', true)],
    missing_apps: [],
    control: control('running', 3),
    revision: 3,
  },
  {
    workflow_id: 'fixture-leo',
    name: 'Leo',
    role: 'Inbox assistant',
    color_role: 'model',
    derived: false,
    status: 'paused',
    task: { label: 'Paused', text: 'Paused. Resume to pick up where they left off.' },
    done_today: 41,
    apps: [app('gmail', 'Gmail', 'google', true)],
    missing_apps: [],
    control: control('paused', 5),
    revision: 5,
  },
  {
    workflow_id: 'fixture-nora',
    name: 'Nora',
    role: 'Bookkeeper',
    color_role: 'workflow',
    derived: false,
    status: 'ready',
    task: { label: 'Next', text: 'Connect Stripe to start' },
    done_today: 0,
    apps: [app('stripe', 'Stripe', 'stripe', false), app('gmail', 'Gmail', 'google', true)],
    missing_apps: [app('stripe', 'Stripe', 'stripe', false)],
    unsupported_apps: ['QuickBooks'],
    control: control('never_started', 0),
    revision: 1,
  },
];

export function fixtureEmployees(): EmployeeSummary[] {
  return parseEmployees(ROWS);
}
