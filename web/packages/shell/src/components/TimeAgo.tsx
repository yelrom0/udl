/**
 * When something happened, said the way a person says it ("2 minutes ago"), with the exact
 * time in the tooltip and in `datetime` for a machine. It says it again as the clock moves.
 */

import { useEffect, useState } from "react";

/** How often the words are worked out again. */
export const TICK = 30_000;

const STEPS: readonly (readonly [number, Intl.RelativeTimeFormatUnit])[] = [
	[60, "second"],
	[60, "minute"],
	[24, "hour"],
	[7, "day"],
	[4.35, "week"],
	[12, "month"],
];

/** `at` (an ISO time) in words, from `now`: "just now", "2 minutes ago", "in 3 hours". */
export function relative(at: string, now: Date = new Date()): string {
	const then = new Date(at);
	if (Number.isNaN(then.getTime())) {
		return at;
	}
	let seconds = (then.getTime() - now.getTime()) / 1000;
	const said = new Intl.RelativeTimeFormat(undefined, { numeric: "auto" });
	for (const [size, unit] of STEPS) {
		if (Math.abs(seconds) < size) {
			return Math.abs(seconds) < 1 ? "just now" : said.format(Math.round(seconds), unit);
		}
		seconds /= size;
	}
	return said.format(Math.round(seconds), "year");
}

export interface TimeAgoProps {
	/** When it happened: an ISO time. */
	readonly at: string;
}

export function TimeAgo({ at }: TimeAgoProps) {
	const [now, setNow] = useState(() => new Date());

	useEffect(() => {
		const timer = setInterval(() => setNow(new Date()), TICK);
		return () => clearInterval(timer);
	}, []);

	const exact = new Date(at);
	return (
		<time dateTime={at} title={Number.isNaN(exact.getTime()) ? at : exact.toLocaleString()}>
			{relative(at, now)}
		</time>
	);
}
