/**
 * --------------
 * - spec.test.ts -
 * --------------
 *
 * What udl answers, as the page reads it: a list it can draw (and one it cannot, field by
 * field), the operations' groups and their order, and the tabs a plugin's page has.
 */

import { describe, expect, it } from "vitest";
import {
	type GeneratedOperation,
	type GeneratedPlugin,
	type GeneratedUISpec,
	generatedUI,
	groupsOf,
	pluginNamed,
	tabsOf,
} from "./spec";

const HINTS = {
	read_only: true,
	destructive: false,
	idempotent: true,
	open_world: false,
	admin: false,
	mcp: true,
};

const UI: GeneratedUISpec = {
	group: null,
	order: 0,
	view: "list",
	icon: null,
	hidden: false,
	confirm_field: null,
};

function operation(
	name: string,
	ui: Partial<GeneratedUISpec> = {},
	overrides: Partial<GeneratedOperation> = {},
): GeneratedOperation {
	return {
		name,
		title: name,
		summary: "",
		description: "",
		hints: HINTS,
		ui: { ...UI, ...ui },
		method: null,
		path: null,
		request_schema: {},
		response_schema: {},
		...overrides,
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

/** A list of one plugin, as udl answers it. */
function answer(overrides: Partial<GeneratedPlugin> = {}): object {
	return { plugins: [plugin(overrides)] };
}

describe("what udl answered", () => {
	it("is a generated UI when every field is one", () => {
		expect(generatedUI(answer())).toEqual({ plugins: [plugin()] });
	});

	it("is not one when it is not an object with plugins", () => {
		expect(generatedUI(null)).toBeNull();
		expect(generatedUI("nope")).toBeNull();
		expect(generatedUI([])).toBeNull();
		expect(generatedUI({})).toBeNull();
		expect(generatedUI({ plugins: "nope" })).toBeNull();
		expect(generatedUI({ plugins: [{ name: 7 }] })).toBeNull();
	});

	it("is not one when a plugin's kind, title or lists are not", () => {
		expect(generatedUI(answer({ kind: 7 as never }))).toBeNull();
		expect(generatedUI(answer({ title: 7 as never }))).toBeNull();
		expect(generatedUI(answer({ operations: "nope" as never }))).toBeNull();
		expect(generatedUI(answer({ event_types: [7] as never }))).toBeNull();
	});

	it("is not one when a form's lists are not", () => {
		const form = { json_schema: {}, read_only: [], secret: [] };

		expect(generatedUI(answer({ settings: { ...form, read_only: "nope" as never } }))).toBeNull();
		expect(generatedUI(answer({ settings: { ...form, secret: [7] as never } }))).toBeNull();
		expect(generatedUI(answer({ settings: "nope" as never }))).toBeNull();
	});

	it("is not one when an account's lists are not", () => {
		const accounts = { connect: ["oauth"], access_levels: ["read"], options_schema: {} };

		expect(generatedUI(answer({ accounts: { ...accounts, connect: "nope" as never } }))).toBeNull();
		expect(
			generatedUI(answer({ accounts: { ...accounts, access_levels: [7] as never } })),
		).toBeNull();
		expect(generatedUI(answer({ accounts: "nope" as never }))).toBeNull();
	});

	it("is not one when an operation's hints are not", () => {
		const one = operation("list_sites");

		expect(
			generatedUI(answer({ operations: [{ ...one, hints: { ...HINTS, mcp: "yes" } } as never] })),
		).toBeNull();
		expect(generatedUI(answer({ operations: [{ ...one, hints: null } as never] }))).toBeNull();
	});

	it("is not one when an operation's view is not one of the four", () => {
		expect(
			generatedUI(answer({ operations: [operation("list_sites", { view: "grid" as never })] })),
		).toBeNull();
	});

	it("is not one when an operation's own fields are not", () => {
		const one = operation("list_sites");

		expect(generatedUI(answer({ operations: [{ ...one, name: 7 } as never] }))).toBeNull();
		expect(generatedUI(answer({ operations: [{ ...one, title: 7 } as never] }))).toBeNull();
		expect(generatedUI(answer({ operations: [{ ...one, summary: 7 } as never] }))).toBeNull();
		expect(generatedUI(answer({ operations: [{ ...one, description: 7 } as never] }))).toBeNull();
		expect(generatedUI(answer({ operations: [{ ...one, method: 7 } as never] }))).toBeNull();
		expect(generatedUI(answer({ operations: [{ ...one, path: 7 } as never] }))).toBeNull();
		expect(generatedUI(answer({ operations: [one] }))).toEqual({
			plugins: [plugin({ operations: [one] })],
		});
	});

	it("is not one when an operation's UI spec is not", () => {
		const one = operation("list_sites");
		const wrong = (ui: Record<string, unknown>): object => ({
			plugins: [{ ...plugin(), operations: [{ ...one, ui: { ...UI, ...ui } }] }],
		});

		expect(generatedUI(wrong({ group: 7 }))).toBeNull();
		expect(generatedUI(wrong({ order: "first" }))).toBeNull();
		expect(generatedUI(wrong({ icon: 7 }))).toBeNull();
		expect(generatedUI(wrong({ hidden: "no" }))).toBeNull();
		expect(generatedUI(wrong({ confirm_field: 7 }))).toBeNull();
	});
});

describe("the plugin a page is for", () => {
	it("is the one udl named, or none", () => {
		const ui = { plugins: [plugin(), plugin({ name: "slack", title: "Slack" })] };

		expect(pluginNamed(ui, "slack")?.title).toBe("Slack");
		expect(pluginNamed(ui, "nothing")).toBeUndefined();
	});
});

describe("an operation's groups", () => {
	it("puts the ungrouped ones first, then the groups by name", () => {
		const one = plugin({
			operations: [
				operation("c", { group: "writing", order: 0 }),
				operation("b", { group: "reading", order: 0 }),
				operation("a", { group: null, order: 0 }),
			],
		});

		expect(groupsOf(one).map((group) => group.group)).toEqual([null, "reading", "writing"]);
	});

	it("puts each group's operations in their order, then by title", () => {
		const one = plugin({
			operations: [
				operation("later", { order: 2 }, { title: "Later" }),
				operation("second", { order: 1 }, { title: "Zebra" }),
				operation("first", { order: 1 }, { title: "Apple" }),
			],
		});

		expect(groupsOf(one)[0]?.operations.map((each) => each.name)).toEqual([
			"first",
			"second",
			"later",
		]);
	});

	it("leaves out the hidden ones, and has no group when none is shown", () => {
		expect(groupsOf(plugin({ operations: [operation("a", { hidden: true })] }))).toEqual([]);
	});

	it("keeps a group with one operation in it", () => {
		const groups = groupsOf(
			plugin({
				operations: [operation("a", { group: "reading" }), operation("b", { group: "reading" })],
			}),
		);

		expect(groups).toHaveLength(1);
		expect(groups[0]?.operations).toHaveLength(2);
	});
});

describe("a plugin's tabs", () => {
	it("are the ones it has something for, in the card's order", () => {
		const one = plugin({
			operations: [operation("a")],
			settings: { json_schema: {}, read_only: [], secret: [] },
			accounts: { connect: ["oauth"], access_levels: ["read"], options_schema: {} },
			event_types: ["gmail.message"],
		});

		expect(tabsOf(one)).toEqual(["operations", "settings", "accounts", "events"]);
	});

	it("leave out a tab it has nothing for", () => {
		expect(tabsOf(plugin())).toEqual([]);
		expect(tabsOf(plugin({ operations: [operation("a", { hidden: true })] }))).toEqual([]);
		expect(tabsOf(plugin({ event_types: ["gmail.message"] }))).toEqual(["events"]);
	});
});
