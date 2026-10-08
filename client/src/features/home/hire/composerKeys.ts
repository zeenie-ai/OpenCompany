/**
 * The hire composer's label rule, kept apart from the component so the
 * component file exports components only (react-refresh) and the rule is
 * testable on its own. Which Enter sends is lib/composerKeys.ts.
 */

/** Create's label: busy, editing the draft, or `idle` (the Welcome guide
 *  says "Create their setup"). */
export function createLabel(working: boolean, refining: boolean, idle = 'Create employee'): string {
  if (working) return 'Creating…';
  return refining ? 'Update' : idle;
}
