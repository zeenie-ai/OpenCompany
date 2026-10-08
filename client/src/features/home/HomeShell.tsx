/**
 * Normal mode ("Home"): the owner-facing screen where AI employees are hired
 * and supervised (design_handoff_opencompany_home, "App shell (Normal
 * mode)"). The team sidebar on the left; on the right the header and the
 * current view, either hiring a new employee or one employee's page.
 *
 * The shell owns what spans views: the employee broadcasts that keep the
 * team current, the orb behind the content, the Workspace dock on the
 * right, the Settings dialog, the Welcome guide (onboarding/, which
 * opens over the hire view on first launch) and, once the guide is
 * finished, the Get started checklist. Connection actions open the app shell's
 * shared credentials dialog. Switching views scrolls to the top and plays
 * the view swap.
 *
 * Hiring scrolls as one page. An employee's page is the chat, which scrolls
 * its conversation itself above its message box, so it gets a column that
 * does not scroll; it reports when its conversation has left the top, for
 * the header's border.
 */

import { useLayoutEffect, useRef, useState } from 'react';
import { animate } from '@/lib/motion';
import { useShellDialogsStore, type CredentialsIntent } from '@/stores/shellDialogsStore';
import { useApprovalLifecycle } from './approvals/data';
import { useEmployeeLifecycle, useEmployeesQuery } from './data/employees';
import type { EmployeeSummary } from './data/schemas';
import { EmployeeView } from './employee/EmployeeView';
import { HomeHeader } from './header/HomeHeader';
import { HireView } from './hire/HireView';
import { GetStartedChecklist } from './onboarding/GetStartedChecklist';
import { WelcomeGuide } from './onboarding/WelcomeGuide';
import { SPIKE, spikeOrb } from './orb/orb';
import { OrbStage } from './orb/OrbStage';
import { HomeSettings } from './settings/HomeSettings';
import { HomeSidebar } from './sidebar/HomeSidebar';
import { useHomeStore } from './state/homeStore';
import { WorkspaceDock } from './workspace/WorkspaceDock';

/** Scrolled further than this, the header draws its bottom border. */
const HEADER_BORDER_AFTER_PX = 6;

/** The employee on screen, from the team list (null while hiring, or
 *  until the list has them). */
function useViewEmployee(): EmployeeSummary | null {
  const view = useHomeStore((s) => s.view);
  const { data: employees } = useEmployeesQuery();
  if (view.kind !== 'employee') return null;
  return employees?.find((employee) => employee.workflow_id === view.workflowId) ?? null;
}

export default function HomeShell() {
  useEmployeeLifecycle();
  useApprovalLifecycle();
  const view = useHomeStore((s) => s.view);
  const employee = useViewEmployee();
  const openCredentials = useShellDialogsStore((s) => s.openCredentials);
  const openConnect = (providerId: string, intent: CredentialsIntent = 'connect') => {
    spikeOrb(SPIKE.connect);
    openCredentials({ providerId, intent });
  };
  const [scrolled, setScrolled] = useState(false);
  const scrollRef = useRef<HTMLDivElement>(null);
  const viewRef = useRef<HTMLDivElement>(null);
  const viewKey = view.kind === 'employee' ? `employee:${view.workflowId}` : 'hire';

  const shownKey = useRef(viewKey);
  useLayoutEffect(() => {
    if (shownKey.current === viewKey) return;
    shownKey.current = viewKey;
    scrollRef.current?.scrollTo({ top: 0 });
    setScrolled(false);
    animate(
      viewRef.current,
      [
        { opacity: 0, transform: 'translateY(14px)', filter: 'blur(4px)' },
        { opacity: 1, transform: 'none', filter: 'blur(0)' },
      ],
      { duration: 'view-swap', easing: 'spring' },
    );
  }, [viewKey]);

  return (
    <div className="relative flex min-h-0 flex-1 antialiased">
      <HomeSidebar />
      <main className="relative flex min-w-0 flex-1 flex-col">
        <OrbStage />
        <HomeHeader
          title={view.kind === 'employee' ? employee?.name ?? 'Employee' : 'New employee'}
          employee={employee}
          scrolled={scrolled}
        />
        {view.kind === 'employee' ? (
          <div className="relative z-10 flex min-h-0 flex-1 flex-col">
            <div ref={viewRef} key={viewKey} className="flex min-h-0 w-full flex-1 flex-col">
              <EmployeeView workflowId={view.workflowId} onConnect={openConnect} onScrolledChange={setScrolled} />
            </div>
          </div>
        ) : (
          <div
            ref={scrollRef}
            onScroll={(event) => setScrolled(event.currentTarget.scrollTop > HEADER_BORDER_AFTER_PX)}
            className="relative z-10 flex min-h-0 flex-1 flex-col overflow-x-hidden overflow-y-auto"
          >
            <div className="mx-auto flex w-full max-w-(--w-home-content) flex-1 flex-col items-center px-6 pt-2 pb-10">
              <div ref={viewRef} key={viewKey} className="flex w-full flex-1 flex-col items-center">
                <HireView onConnect={openConnect} />
              </div>
            </div>
          </div>
        )}
      </main>
      <WorkspaceDock onConnect={openConnect} />
      <HomeSettings onConnect={openConnect} />
      <WelcomeGuide />
      <GetStartedChecklist />
    </div>
  );
}
