/**
 * The logo intro plays once per page load, however many logos mount (the
 * sidebar and the collapsed-sidebar header can both show one).
 */

let played = false;

/** True the first time it is called in a page load, false after. */
export function claimLogoIntro(): boolean {
  if (played) return false;
  played = true;
  return true;
}

/** Forget that the intro played. Tests only. */
export function resetLogoIntroForTests(): void {
  played = false;
}
