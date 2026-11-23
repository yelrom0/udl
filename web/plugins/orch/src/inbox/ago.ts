/**
 * How long ago something happened, in the one line a card shows ("12 min ago"). The inbox says
 * it of every question, of every action, and of its own last read, so a page left open is not
 * mistaken for news.
 */
export function ago(iso: string, now: number = Date.now()): string {
	const then = Date.parse(iso);
	if (Number.isNaN(then)) {
		return "";
	}
	const seconds = Math.max(0, Math.round((now - then) / 1000));
	if (seconds < 60) {
		return `${seconds} s ago`;
	}
	const minutes = Math.round(seconds / 60);
	if (minutes < 60) {
		return `${minutes} min ago`;
	}
	const hours = Math.round(minutes / 60);
	if (hours < 24) {
		return `${hours} h ago`;
	}
	return `${Math.round(hours / 24)} d ago`;
}
