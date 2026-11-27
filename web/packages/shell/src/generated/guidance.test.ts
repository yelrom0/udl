/**
 * -----------------
 * - guidance.test.ts -
 * -----------------
 *
 * How a UI plugin guides the page udl generates (24.7): what it hides, regroups, renames and
 * reorders (by operation, so two plugins' guidance cannot collide), which tabs stay (its own
 * page only), and the drawer entry it asks for.
 */

import type { GeneratedNav } from "@udl/ui-sdk";
import { describe, expect, it } from "vitest";
import type { UIPluginInfo } from "../stores/plugins";
import { guidanceOf, guided, matches, navOf, tabsGuided, tabsOfGuided } from "./guidance";
import type { GeneratedOperation, GeneratedPlugin } from "./spec";

const HINTS = {
	read_only: true,
	destructive: false,
	idempotent: true,
	open_world: false,
	admin: false,
	mcp: true,
};

function operation(
	name: string,
	overrides: { title?: string; group?: string | null; order?: number; hidden?: boolean } = {},
): GeneratedOperation {
	return {
		name,
		title: overrides.title ?? name,
		summary: "",
		description: "",
		hints: HINTS,
		ui: {
			group: overrides.group ?? null,
			order: overrides.order ?? 0,
			view: "list",
			icon: null,
			hidden: overrides.hidden ?? false,
			confirm_field: null,
		},
		method: null,
		path: null,
		request_schema: {},
		response_schema: {},
	};
}

function plugin(...operations: GeneratedOperation[]): GeneratedPlugin {
	return {
		name: "gmail",
		kind: "udl.integrations",
		title: "Gmail",
		summary: "",
		has_ui_plugin: true,
		operations,
		settings: { json_schema: {}, read_only: [], secret: [] },
		accounts: { connect: ["oauth"], access_levels: ["read"], options_schema: {} },
		event_types: ["gmail.message"],
		problems: [],
	};
}

/** A UI plugin, as `/ui/plugins.json` lists it. */
function ui(
	name: string,
	generated: UIPluginInfo["generated"] = null,
	plugin: string | null = null,
): UIPluginInfo {
	return {
		name,
		version: "0.1.0",
		base_url: `/ui/plugins/${name}/`,
		entry_url: `/ui/plugins/${name}/index.js`,
		styles: [],
		isolation: "shadow",
		contributes: {},
		plugin,
		generated,
	};
}

describe("a pattern", () => {
	it("names an operation, or its prefix with a star", () => {
		expect(matches("gmail_send", "gmail_send")).toBe(true);
		expect(matches("gmail_*", "gmail_send_all")).toBe(true);
		expect(matches("gmail_*", "gmail")).toBe(false);
		expect(matches("gmail_send", "gmail_send_all")).toBe(false);
	});
});

describe("the guidance every UI plugin gives", () => {
	it("is nothing when none gives any", () => {
		expect(guidanceOf([], "gmail")).toEqual({
			hide: [],
			show: [],
			groups: {},
			groupOrder: [],
			order: [],
			labels: {},
		});
	});

	it("is what reaches the page: the UI-only manifests', then its own", () => {
		const found = guidanceOf(
			[
				ui("gmail", { hide: ["gmail_send_*"] }, "gmail"),
				ui("shadcn", { hide: ["fake_delete"], labels: { fake_list: "Things" } }),
			],
			"gmail",
		);

		expect(found.hide).toEqual(["fake_delete", "gmail_send_*"]);
		expect(found.labels).toEqual({ fake_list: "Things" });
	});

	it("takes only the guidance that reaches its page", () => {
		// (A bound manifest guides its own plugin's page; a UI-only one guides every page.)
		const manifests = [
			ui("gmail", { hide: ["gmail_list"] }, "gmail"),
			ui("theme", { hide: ["fake_list"] }),
		];

		expect(guidanceOf(manifests, "gmail").hide).toEqual(["fake_list", "gmail_list"]);
		expect(guidanceOf(manifests, "fake").hide).toEqual(["fake_list"]);
		expect(guidanceOf(manifests, "other").hide).toEqual(["fake_list"]);
	});

	it("gives the plugin's own guidance the last word", () => {
		const manifests = [
			ui("gmail", { labels: { gmail_list: "Inbox" } }, "gmail"),
			ui("theme", { labels: { gmail_list: "Mail", gmail_send: "Send" } }),
		];

		expect(guidanceOf(manifests, "gmail").labels).toEqual({
			gmail_list: "Inbox",
			gmail_send: "Send",
		});
	});

	it("keeps a group's own order", () => {
		const found = guidanceOf(
			[ui("gmail", { groups: { Mail: ["gmail_list"], Drafts: ["gmail_drafts"] } }, "gmail")],
			"gmail",
		);

		expect(found.groupOrder).toEqual(["Mail", "Drafts"]);
	});

	it("keeps both manifests' members of a group they both name", () => {
		// (A group is a name, not an owner: the second to say it adds to it, not replaces it.)
		const found = guidanceOf(
			[
				ui("gmail", { groups: { Mail: ["gmail_list"] } }, "gmail"),
				ui("shadcn", { groups: { Mail: ["fake_list"] } }),
			],
			"gmail",
		);

		expect(found.groups.Mail).toEqual(["fake_list", "gmail_list"]);
		expect(found.groupOrder).toEqual(["Mail"]);
	});
});

describe("a guided page", () => {
	it("is the page udl answered when there is no guidance", () => {
		const page = plugin(operation("gmail_list"));

		expect(guided(page, guidanceOf([], "gmail"))).toEqual(page);
	});

	it("hides the operations it names, a star naming a prefix", () => {
		const page = plugin(
			operation("gmail_list"),
			operation("gmail_send"),
			operation("gmail_send_all"),
		);

		const found = guided(
			page,
			guidanceOf([ui("gmail", { hide: ["gmail_send*"] }, "gmail")], "gmail"),
		);

		expect(found.operations.map((one) => one.name)).toEqual(["gmail_list"]);
	});

	it("shows an operation its show names, though a hide matches it (29.9)", () => {
		// (A plugin whose day-to-day operations have pages of their own, and whose setup ones are
		// the page's: `hide: ["orch_*"]` with `show: ["orch_list_runners"]`.)
		const page = plugin(operation("orch_assign"), operation("orch_list_runners"));

		const found = guided(
			page,
			guidanceOf([ui("orch", { hide: ["orch_*"], show: ["orch_list_runners"] }, "orch")], "orch"),
		);

		expect(found.operations.map((one) => one.name)).toEqual(["orch_list_runners"]);
	});

	it("leaves a hidden operation out of the tabs' count too", () => {
		const page = plugin(operation("gmail_list", { hidden: true }));
		const found = guided(
			page,
			guidanceOf([ui("gmail", { hide: ["gmail_list"] }, "gmail")], "gmail"),
		);

		expect(found.operations).toEqual([]);
	});

	it("calls an operation what it says", () => {
		const found = guided(
			plugin(operation("gmail_list", { title: "List messages" })),
			guidanceOf([ui("gmail", { labels: { gmail_list: "Inbox" } }, "gmail")], "gmail"),
		);

		expect(found.operations[0]?.title).toBe("Inbox");
	});

	it("moves an operation under the group it names", () => {
		const found = guided(
			plugin(operation("gmail_list"), operation("gmail_drafts")),
			guidanceOf(
				[ui("gmail", { groups: { Mail: ["gmail_list", "gmail_drafts"] } }, "gmail")],
				"gmail",
			),
		);

		expect(found.operations.map((one) => one.ui.group)).toEqual(["Mail", "Mail"]);
	});

	it("puts the operations it orders first, in that order", () => {
		const page = plugin(
			operation("gmail_list", { order: 0 }),
			operation("gmail_send", { order: 0 }),
			operation("gmail_drafts", { order: 0 }),
		);

		const found = guided(
			page,
			guidanceOf([ui("gmail", { order: ["gmail_drafts", "gmail_list"] }, "gmail")], "gmail"),
		);

		expect([...found.operations].sort((left, right) => left.ui.order - right.ui.order)).toEqual([
			found.operations[2],
			found.operations[0],
			found.operations[1],
		]);
	});
});

describe("the tabs a page keeps", () => {
	it("are udl's own when the UI plugin says nothing", () => {
		expect(tabsGuided(plugin(operation("gmail_list")), null)).toEqual([
			"operations",
			"settings",
			"accounts",
			"events",
		]);
	});

	it("are the ones it names, in its order", () => {
		expect(tabsGuided(plugin(operation("gmail_list")), ["accounts", "operations"])).toEqual([
			"accounts",
			"operations",
		]);
	});

	it("are all of them when its UI plugin names none (the served shape is an empty list)", () => {
		// (udl sends `tabs: []` for a guidance that does not name them, so `[]` is "all of them".)
		expect(tabsOfGuided([ui("gmail", { tabs: [] }, "gmail")], "gmail")).toBeNull();
		expect(tabsGuided(plugin(operation("gmail_list")), [])).toEqual([
			"operations",
			"settings",
			"accounts",
			"events",
		]);
	});

	it("are only the ones the page has something for", () => {
		const only = {
			...plugin(operation("gmail_list")),
			settings: null,
			accounts: null,
			event_types: [],
		};

		expect(tabsGuided(only, ["accounts", "operations"])).toEqual(["operations"]);
	});
});

describe("the drawer's entry", () => {
	it("is udl's rule when the plugin's UI says nothing", () => {
		expect(navOf([ui("gmail", null, "gmail")], "gmail")).toBeNull();
		expect(navOf([], "gmail")).toBeNull();
	});

	it("is what the plugin's own UI plugin asks for", () => {
		const found: GeneratedNav = { title: "Mail", icon: "mail" };

		expect(navOf([ui("gmail", { nav: found }, "gmail")], "gmail")).toEqual(found);
	});

	it("is no entry when it asks for none", () => {
		expect(navOf([ui("gmail", { nav: false }, "gmail")], "gmail")).toBe(false);
	});

	it("is not another plugin's", () => {
		expect(navOf([ui("shadcn", { nav: { title: "Shadcn" } })], "gmail")).toBeNull();
	});
});
