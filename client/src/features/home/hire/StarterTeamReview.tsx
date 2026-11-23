import { useState } from 'react';
import { Button } from '@/components/ui/button';
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from '@/components/ui/dialog';
import { HireTeamEditor, type HireTeamMember } from '../genui';
import type { Starter } from './templates';

export function StarterTeamReview({ starter, busy, onHire, onClose }: {
  starter: Starter; busy: boolean; onHire: (roles: HireTeamMember[]) => Promise<boolean>; onClose: () => void;
}) {
  const [roles, setRoles] = useState(starter.hire.team);
  return <Dialog open onOpenChange={open => { if (!open && !busy) onClose(); }}>
    <DialogContent className="max-h-[85vh] overflow-y-auto">
      <DialogHeader>
        <DialogTitle>Hire {starter.label}</DialogTitle>
        <DialogDescription>{starter.summary}</DialogDescription>
      </DialogHeader>
      <HireTeamEditor team={roles} hiring={busy} onChange={setRoles} />
      <DialogFooter>
        <Button variant="quiet" onClick={onClose} disabled={busy}>Cancel</Button>
        <Button disabled={busy || roles.some(role => !role.name.trim() || !role.responsibility.trim())}
          onClick={async () => { if (await onHire(roles)) onClose(); }}>Hire employee</Button>
      </DialogFooter>
    </DialogContent>
  </Dialog>;
}
