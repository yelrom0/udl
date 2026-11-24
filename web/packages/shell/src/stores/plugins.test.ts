import { renderHook } from "@testing-library/react";
import { act } from "react";
import { beforeEach, describe, expect, it } from "vitest";
import { ownNav } from "../plugins/own";
import { Registry } from "../plugins/registry";
import {
	addError,
	navWithCounts,
	plugins,
	setNavCount,
	setPlugins,
	usePluginErrors,
	usePlugins,
} from "./plugins";

/** The identity a plugin's nav entry carries. */
const identity = { name: "orch", version: "0.1.0", baseUrl: "/ui/plugins/orch/" };

/** The registry with one plugin's page in it. */
function withPage(): Registry {
	const registry = new Registry();
	registry.nav.push({
		plugin: identity,
		title: "Q&A",
		to: "/p/orch/questions",
		route: "questions",
	});
	return registry;
}

const GMAIL = {
	name: "gmail",
	version: "0.1.0",
	base_url: "/ui/plugins/gmail/",
	entry_url: "/ui/plugins/gmail/index.js",
	styles: ["/ui/plugins/gmail/index.css"],
	isolation: "shadow" as const,
	contributes: { nav: [{ title: "Gmail", route: "inbox" }], routes: ["inbox"] },
	plugin: "gmail",
};

beforeEach(() => {
	plugins.setState({ manifests: [], problems: [], errors: [], nav: ownNav(), counts: {} });
});

describe("the plugins store", () => {
	it("knows no plugins at first", () => {
		expect(plugins.getState().manifests).toEqual([]);
		expect(plugins.getState().problems).toEqual([]);
		expect(plugins.getState().errors).toEqual([]);
	});

	it("keeps what udl listed, and the bundles it refused", () => {
		setPlugins([GMAIL], [{ origin: "/tmp/ui/broken", error: "no manifest.json" }]);

		expect(plugins.getState().manifests).toEqual([GMAIL]);
		expect(plugins.getState().manifests[0]?.contributes?.nav?.[0]?.title).toBe("Gmail");
		expect(plugins.getState().problems).toEqual([
			{ origin: "/tmp/ui/broken", error: "no manifest.json" },
		]);
	});

	it("replaces the list rather than adding to it", () => {
		setPlugins([GMAIL], []);
		setPlugins([], []);

		expect(plugins.getState().manifests).toEqual([]);
	});

	it("lists a bundle that would not load, and keeps the others", () => {
		setPlugins([GMAIL], []);

		addError("gmail", "its bundle 404s");

		expect(plugins.getState().errors).toEqual([{ name: "gmail", message: "its bundle 404s" }]);
		expect(plugins.getState().manifests).toHaveLength(1);
	});

	it("says a bundle failed once, however often it is asked", () => {
		setPlugins([GMAIL], []);

		addError("gmail", "its bundle 404s");
		addError("gmail", "its bundle 404s");

		expect(plugins.getState().errors).toHaveLength(1);
	});

	it("hands the manifests and the errors to a component", () => {
		const manifests = renderHook(() => usePlugins());
		const errors = renderHook(() => usePluginErrors());

		act(() => {
			setPlugins([GMAIL], []);
			addError("gmail", "its bundle 404s");
		});

		expect(manifests.result.current).toEqual([GMAIL]);
		expect(errors.result.current).toEqual([{ name: "gmail", message: "its bundle 404s" }]);
	});

	it("starts with the shell's own pages in the navigation", () => {
		expect(plugins.getState().nav.map((item) => item.to)).toContain("/sites");
		expect(plugins.getState().nav[0]).toMatchObject({ title: "Dashboard", to: "/" });
	});
});

describe("the nav's live counts (29.7)", () => {
	it("puts a count on the entry the plugin named", () => {
		const nav = withPage().nav;

		const found = navWithCounts(nav, { "orch:questions": 3 });

		expect(found[0]?.count).toBe(3);
	});

	it("leaves the entries nobody counted alone", () => {
		const nav = withPage().nav;

		const found = navWithCounts([...ownNav(), ...nav], { "orch:questions": 3 });

		// (The shell's own pages are never counted, and a plugin's page with no count is not.)
		expect(found.filter((entry) => entry.count !== undefined).map((entry) => entry.count)).toEqual([
			3,
		]);
	});

	it("takes a count off again", () => {
		setNavCount("orch", "questions", 3);
		expect(plugins.getState().counts["orch:questions"]).toBe(3);

		setNavCount("orch", "questions", null);

		expect(plugins.getState().counts["orch:questions"]).toBeUndefined();
	});

	it("keeps one plugin's count from another's", () => {
		setNavCount("orch", "questions", 1);
		setNavCount("other", "questions", 9);

		expect(plugins.getState().counts).toEqual({ "orch:questions": 1, "other:questions": 9 });
	});
});
