/**
 * A plugin's pages: `/ui/p/<plugin>/<anything>` is one of the paths its manifest lists
 * (`inbox`, `message/$id`), and what the `$name`s were is the props its contribution is drawn
 * with.
 */

/** A page a plugin's bundle has, and what its `$name`s were. */
export interface MatchedRoute {
	/** The path, as the manifest lists it (`message/$id`). */
	readonly route: string;
	/** What each `$name` was (decoded, as a URL's text). */
	readonly params: Readonly<Record<string, string>>;
}

/** The parts of `path` (`message/abc`), and of a route (`message/$id`). */
const partsOf = (path: string): string[] => path.split("/").filter((part) => part !== "");

/**
 * The page `asked` is, among `routes`: the first whose parts line up, with a `$name` taking
 * any one part. `null` when the plugin has no page for it.
 */
export function matchRoute(routes: readonly string[], asked: string): MatchedRoute | null {
	const wanted = partsOf(asked);
	for (const route of routes) {
		const parts = partsOf(route);
		if (parts.length !== wanted.length) {
			continue;
		}
		const params: Record<string, string> = {};
		let matched = true;
		for (const [index, part] of parts.entries()) {
			const given = wanted[index] ?? "";
			if (part.startsWith("$")) {
				params[part.slice(1)] = decodeURIComponent(given);
				continue;
			}
			if (part !== given) {
				matched = false;
				break;
			}
		}
		if (matched) {
			return { route, params };
		}
	}
	return null;
}

/** A query, as the strings a contribution is given (only the ones that are strings). */
export function searchOf(search: Record<string, unknown>): Record<string, string> {
	const said: Record<string, string> = {};
	for (const [name, value] of Object.entries(search)) {
		if (typeof value === "string") {
			said[name] = value;
		}
	}
	return said;
}
