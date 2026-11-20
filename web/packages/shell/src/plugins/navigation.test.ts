/**
 * Where a plugin's `ctx.navigate` goes: the shell's router once the page has one, and the
 * browser's own history before that (a page that is still starting up).
 */

import { describe, expect, it, vi } from "vitest";
import { navigate, onNavigate, routerNavigation } from "./navigation";

describe("a plugin's navigation", () => {
	it("goes through the browser's own history before the page says otherwise", () => {
		const assign = vi.fn();
		vi.spyOn(window, "location", "get").mockReturnValue({
			...window.location,
			assign,
		} as Location);

		navigate("/p/gmail/inbox");

		expect(assign).toHaveBeenCalledWith("/p/gmail/inbox");
		vi.restoreAllMocks();
	});

	it("goes where the page's router says once it is told", () => {
		const go = vi.fn();
		onNavigate(go);

		navigate("/p/gmail/inbox");

		expect(go).toHaveBeenCalledWith("/p/gmail/inbox");
	});

	it("puts the page's base in front of the path, so the router moves and the server is not asked", () => {
		const pushed: string[] = [];
		const go = routerNavigation({ push: (path: string) => pushed.push(path) });

		go("/sites");
		go("p/gmail/inbox");

		expect(pushed).toEqual(["/ui/sites", "/ui/p/gmail/inbox"]);
	});
});
