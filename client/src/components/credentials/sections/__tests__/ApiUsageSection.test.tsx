import { beforeEach, describe, expect, it, vi } from 'vitest';
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';

const mocks = vi.hoisted(() => ({ getAPIUsageSummary: vi.fn() }));

vi.mock('@/hooks/useApiKeys', () => ({
  useApiKeys: () => ({ ...mocks, isConnected: true }),
}));

import ApiUsageSection from '../ApiUsageSection';

const GMAIL = {
  service: 'gmail',
  total_resources: 12,
  total_cost: 0,
  execution_count: 3,
  operations: [
    { operation: 'mail_send', resource_count: 2, total_cost: 0, execution_count: 2 },
    { operation: 'mail_read', resource_count: 10, total_cost: 0, execution_count: 1 },
  ],
};
const CALENDAR = {
  service: 'google_calendar',
  total_resources: 4,
  total_cost: 0,
  execution_count: 1,
  operations: [{ operation: 'event_create', resource_count: 4, total_cost: 0, execution_count: 1 }],
};
const TWITTER = {
  service: 'twitter',
  total_resources: 20,
  total_cost: 0.11,
  execution_count: 2,
  operations: [{ operation: 'posts_read', resource_count: 20, total_cost: 0.11, execution_count: 2 }],
};

async function openSection() {
  await userEvent.setup().click(await screen.findByRole('button', { name: /API Usage/ }));
}

beforeEach(() => {
  vi.clearAllMocks();
  mocks.getAPIUsageSummary.mockResolvedValue([GMAIL, CALENDAR, TWITTER]);
});

describe('ApiUsageSection', () => {
  it('reads every service and keeps the ones the connector lists', async () => {
    render(<ApiUsageSection services={['gmail', 'google_calendar']} serviceName="Google Workspace" />);
    await openSection();

    expect(mocks.getAPIUsageSummary).toHaveBeenCalledWith();
    expect(await screen.findByText('mail_send')).toBeInTheDocument();
    expect(screen.getByText('mail_read')).toBeInTheDocument();
    expect(screen.getByText('event_create')).toBeInTheDocument();
    expect(screen.queryByText('posts_read')).not.toBeInTheDocument();
  });

  it('adds up the calls and resources of the listed services', async () => {
    render(<ApiUsageSection services={['gmail', 'google_calendar']} serviceName="Google Workspace" />);
    await openSection();

    await screen.findByText('mail_send');
    // 3 + 1 calls, 12 + 4 resources.
    expect(screen.getByText('4')).toBeInTheDocument();
    expect(screen.getByText('16')).toBeInTheDocument();
  });

  it('shows the cost of a connector with one service', async () => {
    render(<ApiUsageSection services={['twitter']} serviceName="Twitter" />);
    await openSection();

    expect(await screen.findByText('posts_read')).toBeInTheDocument();
    expect(screen.getAllByText('$0.1100').length).toBeGreaterThan(0);
  });

  it('says there is no usage yet when none of its services has a call', async () => {
    render(<ApiUsageSection services={['google_maps']} serviceName="Google Maps" />);
    await openSection();

    expect(await screen.findByText(/No usage data yet\. Use Google Maps nodes/)).toBeInTheDocument();
  });
});
