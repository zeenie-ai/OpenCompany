/**
 * What a Workspace surface (Browser, Mobile) lets the dock do for Take over:
 * take its screen for the owner, and give it back.
 */
export interface SurfaceControl {
  /** Take the screen; true once the server granted it. */
  claim: () => Promise<boolean>;
  /** Give it back, letting the employee's waiting work on it go on. */
  release: () => void;
}
