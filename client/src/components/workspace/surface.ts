/**
 * What a Workspace surface (Browser, Mobile) lets the dock do for Take over:
 * take its screen for the owner.
 */
export interface SurfaceControl {
  /** Take the screen. Once the server granted it, resolves to the way to give
   *  that screen back, letting the employee's waiting work on it go on; null
   *  when it was refused. Giving back works after this view went away (the
   *  dock moved to another tab or employee), so Hand back always gives back
   *  the screen that was taken. */
  claim: () => Promise<(() => void) | null>;
}

/** A short message for the owner, shown the way the screen hosting the
 *  Workspace shows them (Home: the pill toast; the editor: a toast). */
export type Notify = (message: string, tone: 'success' | 'error') => void;
