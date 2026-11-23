/**
 * The board's filters, kept in the URL (29.4): a filter is a page someone can send, and coming
 * back to the board from a task keeps the state and the text they were looking at.
 */

/** What narrows the board's features pane: a task's state, and text in its title. */
export interface Filters {
	readonly state: string;
	readonly text: string;
}

/** The board's own route, which its filters are written onto. */
export const BOARD = "/ui/p/orch/board";

/** What the query says: the two filters, and nothing for what it does not name. */
export function filtersOf(search: Readonly<Record<string, string>>): Filters {
	return { state: search.state ?? "", text: search.text ?? "" };
}

/**
 * The board's path with a filter changed: a value that is cleared leaves the query, and a query
 * with nothing left is the path alone.
 */
export function filterPath(
	path: string,
	search: Readonly<Record<string, string>>,
	change: Partial<Filters>,
): string {
	const next: Record<string, string> = { ...filtersOf(search), ...change };
	const query = new URLSearchParams();
	for (const [key, value] of Object.entries(next)) {
		if (value !== "") {
			query.set(key, value);
		}
	}
	const written = query.toString();
	return written === "" ? path : `${path}?${written}`;
}
