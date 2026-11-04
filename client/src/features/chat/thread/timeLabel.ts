/**
 * When a message was written, the way a person says it: the time today,
 * "Yesterday, 9:41 AM", the weekday within a week, else the date. Empty when
 * the timestamp cannot be read. The thread's day dividers name the day alone
 * ("Today", "Yesterday", ...), over the first message of each day (`dayKey`).
 */

const DAY_MS = 86_400_000;

function startOfDay(date: Date): number {
  return new Date(date.getFullYear(), date.getMonth(), date.getDate()).getTime();
}

function readDate(timestamp: string | null): Date | null {
  if (!timestamp) return null;
  const date = new Date(timestamp);
  return Number.isNaN(date.getTime()) ? null : date;
}

/** An earlier day than today's: "Yesterday", the weekday within a week, else
 *  the date (with its year when not this one's); null for today. */
function pastDay(date: Date, now: Date): string | null {
  // Rounded: a day with a clock change is 23 or 25 hours long.
  const days = Math.round((startOfDay(now) - startOfDay(date)) / DAY_MS);
  if (days <= 0) return null;
  if (days === 1) return 'Yesterday';
  if (days < 7) return date.toLocaleDateString(undefined, { weekday: 'long' });
  const year = date.getFullYear() === now.getFullYear() ? undefined : 'numeric';
  return date.toLocaleDateString(undefined, { month: 'short', day: 'numeric', year });
}

export function timeLabel(timestamp: string | null, now: Date = new Date()): string {
  const date = readDate(timestamp);
  if (!date) return '';
  const time = date.toLocaleTimeString(undefined, { hour: 'numeric', minute: '2-digit' });
  const day = pastDay(date, now);
  return day ? `${day}, ${time}` : time;
}

/** The local day a message was written on, the same for every message of
 *  that day; null when the timestamp cannot be read. */
export function dayKey(timestamp: string | null): string | null {
  const date = readDate(timestamp);
  return date ? `${date.getFullYear()}-${date.getMonth() + 1}-${date.getDate()}` : null;
}

/** A day divider's label: "Today", "Yesterday", the weekday within a week,
 *  else the date. Empty when the timestamp cannot be read. */
export function dayLabel(timestamp: string | null, now: Date = new Date()): string {
  const date = readDate(timestamp);
  return date ? (pastDay(date, now) ?? 'Today') : '';
}
