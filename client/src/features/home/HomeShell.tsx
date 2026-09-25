/**
 * Normal mode ("Home"): the owner-facing screen where AI employees are hired
 * and supervised (design_handoff_opencompany_home, "App shell (Normal
 * mode)"). The team sidebar on the left; on the right the header and the
 * current view, either hiring a new employee or one employee's card.
 *
 * The shell owns what spans views: the employee broadcasts that keep the
 * team current, the orb behind the content, the Workspace dock on the
 * right, the Settings dialog, and the connect dialog any view can open. Switching views scrolls to the top and
 * plays the view swap.
 */

import { useLayoutEffect, useRef, useState } from 'react';
import { animate } from '@/lib/motion';
import { useApprovalLifecycle } from './approvals/data';
import { useEmployeeLifecycle, useEmployeesQuery } from './data/employees';
import { EmployeeView } from './employee/EmployeeView';
import { HomeHeader } from './header/HomeHeader';
import { HireView } from './hire/HireView';
import { SPIKE, spikeOrb } from './orb/orb';
import { OrbStage } from './orb/OrbStage';
import { ConnectDialog } from './settings/ConnectDialog';
import { HomeSettings } from './settings/HomeSettings';
import { HomeSidebar } from './sidebar/HomeSidebar';
import { useHomeStore } from './state/homeStore';
import { WorkspaceDock } from './workspace/WorkspaceDock';

/** Scrolled further than this, the header draws its bottom border. */
const HEADER_BORDER_AFTER_PX = 6;

function useViewTitle(): string {
  const view = useHomeStore((s) => s.view);
  const { data: employees } = useEmployeesQuery();
  if (view.kind !== 'employee') return 'New employee';
  return employees?.find((employee) => employee.workflow_id === view.workflowId)?.name ?? 'Employee';
}

export default function HomeShell() {
  useEmployeeLifecycle();
  useApprovalLifecycle();
  const view = useHomeStore((s) => s.view);
  const title = useViewTitle();
  const [connectId, setConnectId] = useState<string | null>(null);
  const openConnect = (providerId: string) => {
    spikeOrb(SPIKE.connect);
    setConnectId(providerId);
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
        <HomeHeader title={title} scrolled={scrolled} />
        <div
          ref={scrollRef}
          onScroll={(event) => setScrolled(event.currentTarget.scrollTop > HEADER_BORDER_AFTER_PX)}
          className="relative z-10 min-h-0 flex-1 overflow-x-hidden overflow-y-auto"
        >
          <div className="mx-auto flex max-w-(--w-home-content) flex-col items-center px-6 pt-2 pb-10">
            <div ref={viewRef} key={viewKey} className="flex w-full flex-col items-center">
              {view.kind === 'employee' ? (
                <EmployeeView workflowId={view.workflowId} onConnect={openConnect} />
              ) : (
                <HireView onConnect={openConnect} />
              )}
            </div>
            <p className="m-0 pt-7 text-center text-xs text-fg-muted">
              Your employees ask before sending anything on your behalf.
            </p>
          </div>
        </div>
      </main>
      <WorkspaceDock />
      <HomeSettings onConnect={openConnect} />
      <ConnectDialog providerId={connectId} onClose={() => setConnectId(null)} />
    </div>
  );
}
