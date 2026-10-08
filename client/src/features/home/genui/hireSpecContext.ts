/**
 * What the setup screen's components need beyond their own props: the
 * state the screen was written with (the routine's "When" step is
 * rewritten only once the owner changes when they work), the apps that
 * can start the work (the schedule editor offers only those), and whether
 * they sit in the footer strip. HireScreen provides it.
 */

import { createContext } from 'react';
import type { UiState } from './expressions';

export interface HireSpecContextValue {
  /** The screen's state as normalised, before the owner changed anything. */
  writtenState: UiState;
  /** Apps that can start the work: any name the reply used -> the app's own name. */
  triggerApps: Readonly<Record<string, string>>;
  /** Drawn in the card's footer strip (Ask first, Change something, Hire),
   *  which styles the toggle and the buttons its own way. */
  inFooter?: boolean;
}

export const HireSpecContext = createContext<HireSpecContextValue>({ writtenState: {}, triggerApps: {} });
