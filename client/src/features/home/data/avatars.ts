import builder from '@/assets/employee-avatars/builder.png';
import grower from '@/assets/employee-avatars/grower.png';
import chiefOfStaff from '@/assets/employee-avatars/chief-of-staff.png';
import frontDesk from '@/assets/employee-avatars/front-desk.png';
import researcher from '@/assets/employee-avatars/researcher.png';
import treasurer from '@/assets/employee-avatars/treasurer.png';

// Keep this order and the hash algorithm fixed: changing either would change
// existing employees' faces. These are portrait choices, not role assignments.
const PORTRAITS = [builder, grower, chiefOfStaff, frontDesk, researcher, treasurer] as const;

/** A stable, random-looking portrait for every employee, including workflows
 *  created in Dev mode. The permanent workflow ID keeps it unchanged across
 *  reloads, devices, renames and status updates without storing another field. */
export function getDefaultEmployeeAvatar(workflowId: string): string {
  // FNV-1a over the complete string also supports legacy nonnumeric IDs.
  let hash = 0x811c9dc5;
  for (let index = 0; index < workflowId.length; index++) {
    hash = Math.imul(hash ^ workflowId.charCodeAt(index), 0x01000193) >>> 0;
  }
  return PORTRAITS[hash % PORTRAITS.length];
}
