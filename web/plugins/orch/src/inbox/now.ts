import { useEffect, useState } from "react";

/**
 * The time, kept fresh every `step` milliseconds: a line that says how long ago something was
 * would otherwise be frozen at the moment the page was drawn, and a page left open for an hour
 * would still say "0 s ago".
 */
export function useNow(step = 30_000): number {
	const [now, setNow] = useState(() => Date.now());
	useEffect(() => {
		const tick = setInterval(() => {
			setNow(Date.now());
		}, step);
		return () => {
			clearInterval(tick);
		};
	}, [step]);
	return now;
}
