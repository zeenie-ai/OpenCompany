/**
 * The composer's key and label rules, kept apart from the component so the
 * component file exports components only (react-refresh) and the rules are
 * testable on their own.
 */

import type { KeyboardEvent } from 'react';

type SendKeyEvent = Pick<KeyboardEvent, 'key' | 'shiftKey'> & {
  keyCode?: number;
  nativeEvent?: { isComposing?: boolean; keyCode?: number };
};

/** True for the Enter that should send: not Shift+Enter, and not the Enter
 *  that confirms an IME composition (`isComposing`, or keyCode 229 on
 *  engines that report the composition that way). */
export function isSendKey(event: SendKeyEvent): boolean {
  if (event.key !== 'Enter' || event.shiftKey) return false;
  if (event.nativeEvent?.isComposing) return false;
  return (event.keyCode ?? event.nativeEvent?.keyCode) !== 229;
}

export function createLabel(working: boolean, refining: boolean): string {
  if (working) return 'Creating…';
  return refining ? 'Update' : 'Create employee';
}
