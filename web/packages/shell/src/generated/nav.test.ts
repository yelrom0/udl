/**
 * --------------
 * - nav.test.ts -
 * --------------
 *
 * The drawer's entries for the generated pages (24.6): a plugin with something on its page gets
 * one, and the shell itself, a plugin with nothing to show and one whose UI plugin brings its own
 * navigation do not.
 */

import { describe, expect, it } from "vitest";
import type { UIPluginInfo } from "../stores/plugins";
import { generatedNav, hasSomething } from "./nav";
import type { GeneratedOperation, GeneratedPlugin, GeneratedUI } from "./spec";

const HINTS = {
	read_only: true,
	destructive: false,
	idempotent: true,
	open_world: false,
	admin: false,
	mcp: true,
};

function operation(hidden = false): GeneratedOperation {
	return {
		name: "list_things",
		title: "List things",
		summary: "",
		description: "",
		hints: HINTS,
		ui: { group: null, order: 0, view: "list", icon: null, hidden, confirm_field: null },
		method: null,
		path: null,
		request_schema: {},
		response_schema: {},
	};
}

function plugin(overrides: Partial<GeneratedPlugin> = {}): GeneratedPlugin {
	return {
		name: "gmail",
		kind: "udl.integrations",
		title: "Gmail",
		summary: "",
		has_ui_plugin: false,
		operations: [],
		settings: null,
		accounts: null,
		event_types: [],
		...overrides,
	};
}

function ui(...plugins: GeneratedPlugin[]): GeneratedUI {
	return { plugins };
}

/** A UI plugin, with `generated` as its guidance (24.7). */
function guiding(name: string, generated: NonNullable<UIPluginInfo["generated"]>): UIPluginInfo {
	return { ...ownNavPlugin(name), contributes: {}, generated };
}

/** A UI plugin that brings navigation of its own. */
function ownNavPlugin(name: string): UIPluginInfo {
	return {
		name,
		version: "0.1.0",
		base_url: `/ui/plugins/${name}/`,
		entry_url: `/ui/plugins/${name}/index.js`,
		styles: [],
		isolation: "shadow",
		contributes: { nav: [{ title: "Inbox", route: "inbox" }] },
		plugin: name,
		generated: null,
	};
}

describe("a plugin with something on its page", () => {
	it("has an operation to run, settings or accounts", () => {
		expect(hasSomething(plugin({ operations: [operation()] }))).toBe(true);
		expect(hasSomething(plugin({ settings: { json_schema: {}, read_only: [], secret: [] } }))).toBe(
			true,
		);
		expect(
			hasSomething(
				plugin({ accounts: { connect: ["oauth"], access_levels: ["read"], options_schema: {} } }),
			),
		).toBe(true);
	});

	it("has nothing when its operations are hidden and it has neither settings nor accounts", () => {
		expect(hasSomething(plugin({ operations: [operation(true)] }))).toBe(false);
		expect(hasSomething(plugin())).toBe(false);
	});
});

describe("the drawer's generated entries", () => {
	it("is one per plugin with something to show", () => {
		expect(generatedNav(ui(plugin({ operations: [operation()] })), [])).toEqual([
			{
				plugin: { name: "gmail", version: "", baseUrl: "/ui/" },
				title: "Gmail",
				to: "/p/gmail/",
			},
		]);
	});

	it("is none at all before udl has answered", () => {
		expect(generatedNav(null, [])).toEqual([]);
	});

	it("leaves the shell itself out", () => {
		expect(generatedNav(ui(plugin({ name: "udl", operations: [operation()] })), [])).toEqual([]);
	});

	it("leaves a plugin with nothing to show out", () => {
		expect(generatedNav(ui(plugin()), [])).toEqual([]);
	});

	it("leaves a plugin whose UI plugin brings its own navigation out", () => {
		const gmail = plugin({ operations: [operation()] });
		const feed = plugin({ name: "feed", title: "Feed", operations: [operation()] });

		expect(generatedNav(ui(gmail, feed), [ownNavPlugin("gmail")])).toEqual([
			{
				plugin: { name: "feed", version: "", baseUrl: "/ui/" },
				title: "Feed",
				to: "/p/feed/",
			},
		]);
	});
});

describe("the entry a UI plugin asks for (24.7)", () => {
	it("says what it says, with its icon", () => {
		const gmail = plugin({ operations: [operation()] });

		expect(
			generatedNav(ui(gmail), [guiding("gmail", { nav: { title: "Mail", icon: "mail" } })]),
		).toEqual([
			{
				plugin: { name: "gmail", version: "", baseUrl: "/ui/" },
				title: "Mail",
				icon: "mail",
				to: "/p/gmail/",
			},
		]);
	});

	it("is no entry when it asks for none", () => {
		const gmail = plugin({ operations: [operation()] });

		expect(generatedNav(ui(gmail), [guiding("gmail", { nav: false })])).toEqual([]);
	});

	it("is none when its guidance hides everything it had", () => {
		const gmail = plugin({ operations: [operation()] });

		expect(generatedNav(ui(gmail), [guiding("gmail", { hide: ["list_*"] })])).toEqual([]);
	});

	it("is there still when another plugin's guidance hides its operations", () => {
		// (A bound manifest guides its own plugin's page only.)
		const gmail = plugin({ operations: [operation()] });
		const fake = plugin({ name: "fake", title: "Fake", operations: [operation()] });

		const entries = generatedNav(ui(gmail, fake), [guiding("gmail", { hide: ["list_*"] })]);

		expect(entries.map((entry) => entry.plugin.name)).toEqual(["fake"]);
	});
});
