/** `n` with thousands separators, as a person reads a count. */
export function counted(n: number): string {
	return n.toLocaleString("en-AU");
}

/** `n` as money, as a person reads it: `$8` for whole dollars, and always two decimals for
 * part of one (`$6.30`, `$0.10`), since a key's balance is money, not an approximation. */
export function money(n: number): string {
	return `$${n.toLocaleString("en-AU", {
		minimumFractionDigits: Number.isInteger(n) ? 0 : 2,
		maximumFractionDigits: 2,
	})}`;
}
