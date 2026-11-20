/**
 * Formatters shared by more than one screen.
 */

/** A USD cost: "<$0.01" for an amount under a cent, else two decimals
 *  ("$0.42", "$12.00"). Zero is "$0.00". */
export function usd(amount: number): string {
  if (amount > 0 && amount < 0.01) return '<$0.01';
  return `$${amount.toFixed(2)}`;
}
