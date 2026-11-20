/**
 * A plugin's pages: `/ui/p/<plugin>/<anything>` is one of the paths its manifest lists
 * (`inbox`, `message/$id`), and what the `$name`s were is the props its contribution is drawn
 * with.
 */

import { describe, expect, it } from "vitest";
import { matchRoute, searchOf } from "./routes";

describe("matching a plugin's page", () => {
	it("has no page for a part that is not a URL's text", () => {
		expect(matchRoute(["message/$id"], "message/%E0%A4%A")).toBeNull();
	});

	it("finds the path a plain one names", () => {
		expect(matchRoute(["inbox", "compose"], "inbox")).toEqual({
			route: "inbox",
			params: {},
		});
	});

	it("fills a $name with what the page's path had", () => {
		expect(matchRoute(["message/$id"], "message/abc123")).toEqual({
			route: "message/$id",
			params: { id: "abc123" },
		});
	});

	it("takes the first path that matches, as the manifest lists them", () => {
		expect(matchRoute(["inbox", "inbox/$tab"], "inbox")).toEqual({ route: "inbox", params: {} });
	});

	it("decodes what a $name was, as a URL's own text", () => {
		expect(matchRoute(["message/$id"], "message/a%20b")).toEqual({
			route: "message/$id",
			params: { id: "a b" },
		});
	});

	it("says nothing matches a path the plugin has no page for", () => {
		expect(matchRoute(["inbox"], "compose")).toBeNull();
		expect(matchRoute(["message/$id"], "message")).toBeNull();
		expect(matchRoute([], "inbox")).toBeNull();
	});

	it("takes a path with more parts than the page has as no match", () => {
		expect(matchRoute(["message/$id"], "message/abc/replies")).toBeNull();
	});

	it("hands a page's query on as strings, and leaves what is not one out", () => {
		expect(searchOf({ q: "unread", page: "2", all: true, none: null })).toEqual({
			q: "unread",
			page: "2",
		});
	});
});
