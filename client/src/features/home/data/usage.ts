/**
 * Settings > Billing's usage (`get_employee_usage`): the team's completed
 * tasks so far this month, counted on the server from the run records in
 * the owner's time zone, and `since`, the day that month began there
 * (YYYY-MM-DD). Refetched whenever Billing opens, because runs finish while
 * it is closed.
 */

import { useQuery } from '@tanstack/react-query';
import { z } from 'zod';
import { useWebSocketActions } from '@/contexts/WebSocketContext';

export const EMPLOYEE_USAGE_QUERY_KEY = ['employeeUsage'] as const;

const usageSchema = z.object({
  tasks_this_month: z.number().int().min(0),
  since: z.string().regex(/^\d{4}-\d{2}-\d{2}$/),
});

export interface EmployeeUsage {
  tasksThisMonth: number;
  /** The first day of the counted month, as YYYY-MM-DD. */
  since: string;
}

export function useEmployeeUsageQuery() {
  const { sendRequest, isReady } = useWebSocketActions();
  return useQuery<EmployeeUsage, Error>({
    queryKey: EMPLOYEE_USAGE_QUERY_KEY,
    queryFn: async () => {
      const response = await sendRequest<{ success?: boolean; error?: string }>('get_employee_usage', {});
      if (response?.success === false) throw new Error(response.error || "Couldn't count this month's tasks");
      const parsed = usageSchema.safeParse(response);
      if (!parsed.success) throw new Error("Couldn't count this month's tasks");
      return { tasksThisMonth: parsed.data.tasks_this_month, since: parsed.data.since };
    },
    enabled: isReady,
    refetchOnMount: 'always',
  });
}
