import type { AgentUsage } from "../model";

/** `n` as money, as a person reads it: `$8`, `$6.36` (udl sends a `Decimal` as a string, which
 * `usageOf` already read as a number). */
function money(n: number): string {
	return `$${n.toLocaleString("en-AU", { maximumFractionDigits: 2 })}`;
}

/** `n` with thousands separators, as a person reads a count. */
function counted(n: number): string {
	return n.toLocaleString("en-AU");
}

/** A moment as `YYYY-MM-DD HH:MM` in the reader's own time zone: an allowance resets at 00:00
 * UTC, which is a different morning here. */
export function momentOf(at: string): string {
	const when = new Date(at);
	if (Number.isNaN(when.getTime())) {
		return "";
	}
	const two = (n: number): string => String(n).padStart(2, "0");
	return `${when.getFullYear()}-${two(when.getMonth() + 1)}-${two(when.getDate())} ${two(
		when.getHours(),
	)}:${two(when.getMinutes())}`;
}

/**
 * What an agent has left, in one line (27.14's `line`, read here from `orch_usage`): a paid
 * key's money left of what it may spend (`$6.36 of $8 left`), a free model's requests left of
 * the account's allowance with its reset (`588 of 1,000 requests left, resets 2026-11-29
 * 11:00`), or a harness's own limit when its session is out of quota. Empty when there is
 * nothing to measure — a page says nothing rather than "nothing to measure".
 */
export function usageLineOf(usage: AgentUsage): string {
	if (usage.limited_until !== null) {
		const until = momentOf(usage.limited_until);
		if (until !== "") {
			return `limited until ${until}`;
		}
	}
	if (usage.paid !== null && usage.paid.left !== null) {
		const of = usage.paid.limit === null ? "" : ` of ${money(usage.paid.limit)}`;
		return `${money(usage.paid.left)}${of} left`;
	}
	if (usage.free !== null) {
		const when = momentOf(usage.free.resets_at);
		const resets = when === "" ? "" : `, resets ${when}`;
		return `${counted(usage.free.left)} of ${counted(usage.free.allowance)} requests left${resets}`;
	}
	return "";
}

/** The colour of what is left: plain when fine, a warning when low, an error when out. */
export function usageToneOf(usage: AgentUsage): string {
	if (usage.state === "out") {
		return "text-error";
	}
	if (usage.state === "low" || usage.limited_until !== null) {
		return "text-warning";
	}
	return "text-base-content/60";
}

/**
 * The rest of what an agent has left, as an agent's page says it: what its key has spent of its
 * limit and today, and what its own sessions sent, were given and cost.
 */
export function usageWordsOf(usage: AgentUsage): string[] {
	const words: string[] = [];
	if (usage.paid !== null) {
		const limit = usage.paid.limit === null ? "" : ` of ${money(usage.paid.limit)}`;
		if (usage.paid.spent !== null) {
			words.push(`Spent ${money(usage.paid.spent)}${limit}`);
		} else if (usage.paid.limit !== null) {
			words.push(`Of ${money(usage.paid.limit)}`);
		}
		if (usage.paid.spent_today !== null) {
			words.push(`${money(usage.paid.spent_today)} today`);
		}
	}
	const today = [
		`${counted(usage.requests_today)} ${usage.requests_today === 1 ? "request" : "requests"}`,
	];
	if (usage.tokens_in > 0 || usage.tokens_out > 0) {
		today.push(`${counted(usage.tokens_in)} in`, `${counted(usage.tokens_out)} out`);
	}
	if (usage.cost !== null) {
		today.push(`${money(usage.cost)}`);
	}
	words.push(today.join(", "));
	return words;
}
