import { Button } from '@/components/ui/button';
import type { HireTeamMember } from './hirePayload';

export function HireTeamEditor({ team, onChange, hiring = false }: {
  team: HireTeamMember[]; onChange: (next: HireTeamMember[]) => void; hiring?: boolean;
}) {
  function editTeamMember(key: string, patch: Partial<Omit<HireTeamMember, 'key'>>) {
    onChange(team.map(member => {
      if (member.key === key) return { ...member, ...patch };
      if (member.reports_to === key && (patch.kind === 'specialist' || (patch.reports_to && patch.reports_to !== 'orchestrator'))) {
        return { ...member, reports_to: 'orchestrator' };
      }
      return member;
    }));
  }
  function removeTeamMember(key: string) {
    onChange(team.filter(member => member.key !== key).map(member => member.reports_to === key
      ? { ...member, reports_to: 'orchestrator' } : member));
  }
  function addTeamMember() {
    const key = team.some(member => member.key === 'specialist') ? 'specialist-2' : 'specialist';
    onChange([...team, { key, name: 'Specialist', responsibility: 'Complete assigned work', kind: 'specialist', reports_to: 'orchestrator' }]);
  }
  return (
    <details open className="rounded-row border border-border-default px-3.5 py-3 text-sm text-fg-muted">
      <summary className="cursor-pointer font-semibold text-fg-default">Their team</summary>
      <p className="mt-2">Your Chat Employee assigns work to the Orchestrator and replies to you after reviewing the result.</p>
      <div aria-label="Reporting lines" className="my-3 border-l border-border-default pl-3 text-fg-default">
        <div>Chat Employee</div>
        <div className="ml-3">↳ Orchestrator</div>
        {team.filter(member => member.reports_to === 'orchestrator').map(manager =>
          <div key={manager.key} className="ml-6">↳ {manager.name}
            {team.filter(child => child.reports_to === manager.key).map(child =>
              <div key={child.key} className="ml-3">↳ {child.name}</div>)}
          </div>)}
      </div>
      <ul className="m-0 space-y-3 pl-0">
        {team.map((member) => <li key={member.key} className="mt-3 space-y-1">
          <label className="block">Role name
            <input className="block w-full rounded-row border border-border-default bg-transparent px-2 py-1" value={member.name}
              maxLength={40} disabled={hiring} onChange={(event) => editTeamMember(member.key, { name: event.target.value })} />
          </label>
          <label className="block">Responsibility
            <input className="block w-full rounded-row border border-border-default bg-transparent px-2 py-1" value={member.responsibility}
              maxLength={280} disabled={hiring} onChange={(event) => editTeamMember(member.key, { responsibility: event.target.value })} />
          </label>
          <label className="mr-3">Role type{' '}
            <select value={member.kind} disabled={hiring} onChange={(event) => editTeamMember(member.key,
              { kind: event.target.value === 'employee' ? 'employee' : 'specialist' })}>
              <option value="specialist">Specialist</option><option value="employee">AI Employee</option>
            </select>
          </label>
          <label>Reports to{' '}
            <select value={member.reports_to} disabled={hiring} onChange={(event) => editTeamMember(member.key, { reports_to: event.target.value })}>
              <option value="orchestrator">Orchestrator</option>
              {team.filter((parent) => parent.key !== member.key && parent.kind === 'employee' && parent.reports_to === 'orchestrator')
                .map((parent) => <option key={parent.key} value={parent.key}>{parent.name}</option>)}
            </select>
          </label>
          <Button variant="quiet" disabled={hiring} onClick={() => removeTeamMember(member.key)}>Remove role</Button>
        </li>)}
      </ul>
      {team.length < 2 && <Button variant="quiet" disabled={hiring} onClick={addTeamMember}>Add role</Button>}
    </details>
  );
}
