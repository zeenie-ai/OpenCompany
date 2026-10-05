import { useEmployeesQuery } from '../data/employees';
import { EmployeeAccess } from '../employee/EmployeeAccess';

export function AccessTab() {
  const employees = useEmployeesQuery();
  return <div className="flex flex-col gap-4 p-5">
    <h2 className="m-0 text-lg font-medium">App access</h2>
    <p className="m-0 text-sm text-fg-muted">Choose which apps each employee’s team may use. Sending anything still follows your approval rule.</p>
    {employees.isError && <p role="alert">Employees could not be loaded. Try again.</p>}
    {(employees.data || []).map((employee) => <EmployeeAccess key={employee.workflow_id} workflowId={employee.workflow_id} name={employee.name} />)}
  </div>;
}
