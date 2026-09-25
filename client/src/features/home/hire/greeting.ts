/** The hire view's greeting for the owner's local time of day. */
export function greetingFor(now: Date): string {
  const hour = now.getHours();
  if (hour < 5) return 'Late night';
  if (hour < 12) return 'Good morning';
  if (hour < 18) return 'Good afternoon';
  return 'Good evening';
}
