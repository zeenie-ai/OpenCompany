/**
 * Formatters shared by more than one screen.
 */

/** A USD cost: "<$0.01" for an amount under a cent, else two decimals
 *  ("$0.42", "$12.00"). Zero is "$0.00". */
export function usd(amount: number): string {
  if (amount > 0 && amount < 0.01) return '<$0.01';
  return `$${amount.toFixed(2)}`;
}

/** "Oct 1": a YYYY-MM-DD day from the server, read as a calendar date, not
 *  an instant. */
export function monthDay(date: string): string {
  return new Intl.DateTimeFormat(undefined, { month: 'short', day: 'numeric', timeZone: 'UTC' }).format(new Date(`${date}T00:00:00Z`));
}
