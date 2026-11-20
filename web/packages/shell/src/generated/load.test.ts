/**
 * ---------------
 * - load.test.ts -
 * ---------------
 *
 * `GET /ui/generated.json`, into the store: the JSON udl sends, the error it sends instead, a
 * page that cannot read it at all, and the navigation entry a plugin with something to show
 * gets — unless its UI plugin brings its own pages.
 */

import { beforeEach, describe, expect, it } from "vitest";
import { generated, setGenerated } from "../stores/generated";
import { plugins, type UIPluginInfo } from "../stores/plugins";
import { GENERATED_URL, loadGenerated } from "./load";
import { generatedNav } from "./nav";
import type { GeneratedOperation, GeneratedPlugin, GeneratedUISpec } from "./spec";

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

function operation(name: string): GeneratedOperation {
	return {
		name,
		title: name,
		summary: "",
		description: "",
		hints: HINTS,
		ui: UI,
		method: null,
		path: null,
		request_schema: {},
		response_schema: {},
	};
}

function plugin(name: string, overrides: Partial<GeneratedPlugin> = {}): GeneratedPlugin {
	return {
		name,
		kind: null,
		title: name,
		summary: "",
		has_ui_plugin: false,
		operations: [operation(`${name}_list`)],
		settings: null,
		accounts: null,
		event_types: [],
		...overrides,
	};
}

/** A `fetch` that answers `answer` for the generated list, as the page's own does. */
function answering(answer: { status?: number; json?: unknown } | Error): typeof globalThis.fetch {
	return async () => {
		if (answer instanceof Error) throw answer;
		return new Response(JSON.stringify(answer.json ?? null), {
			status: answer.status ?? 200,
			headers: { "Content-Type": "application/json" },
		});
	};
}

beforeEach(() => {
	setGenerated(null, null);
	plugins.setState({ manifests: [], problems: [], errors: [], nav: [] });
});

describe("loading the generated UI", () => {
	it("reads udl's answer into the store", async () => {
		await loadGenerated(answering({ json: { plugins: [plugin("udl"), plugin("gmail")] } }));

		expect(generated.getState().ui?.plugins.map((one) => one.name)).toEqual(["udl", "gmail"]);
		expect(generated.getState().error).toBeNull();
	});

	it("asks udl's own URL, with the page's cookie", async () => {
		const sent: (RequestInfo | URL)[] = [];
		await loadGenerated(async (input) => {
			sent.push(input);
			return new Response(JSON.stringify({ plugins: [] }));
		});

		expect(sent).toEqual([GENERATED_URL]);
		expect(GENERATED_URL).toBe("/ui/generated.json");
	});

	it("says the status when udl refuses it", async () => {
		await loadGenerated(answering({ status: 403, json: { message: "no" } }));

		expect(generated.getState().ui).toBeNull();
		expect(generated.getState().error).toBe("udl answered 403");
	});

	it("says what udl answered when it is not a generated UI", async () => {
		await loadGenerated(answering({ json: { plugins: "nope" } }));

		expect(generated.getState().ui).toBeNull();
		expect(generated.getState().error).toBe("udl answered something that is not a generated UI");
	});

	it("says why it could not be read at all", async () => {
		await loadGenerated(answering(new Error("offline")));

		expect(generated.getState().ui).toBeNull();
		expect(generated.getState().error).toBe("offline");
	});

	it("says what it was told, when what was thrown is not an error", async () => {
		await loadGenerated(async () => {
			throw "the network said no";
		});

		expect(generated.getState().ui).toBeNull();
		expect(generated.getState().error).toBe("the network said no");
	});
});

describe("the navigation a generated plugin gets", () => {
	const withNav: UIPluginInfo = {
		name: "gmail",
		version: "1.0.0",
		base_url: "/ui/plugins/gmail/",
		entry_url: "/ui/plugins/gmail/index.js",
		styles: [],
		isolation: "shadow",
		contributes: { nav: [{ title: "Gmail", route: "inbox" }], routes: ["inbox"] },
		plugin: "gmail",
	};

	it("has an entry for every plugin with something to show", () => {
		const ui = {
			plugins: [
				plugin("udl"),
				plugin("gmail", {
					kind: "udl.integrations",
					title: "Gmail",
					operations: [],
					accounts: { connect: ["oauth"], access_levels: ["read"], options_schema: {} },
				}),
			],
		};

		expect(generatedNav(ui, []).map((entry) => [entry.title, entry.to])).toEqual([
			["Gmail", "/p/gmail/"],
		]);
	});

	it("leaves out udl itself, and a plugin with nothing to show", () => {
		const ui = {
			plugins: [plugin("udl"), plugin("nothing", { title: "Nothing", operations: [] })],
		};

		expect(generatedNav(ui, [])).toEqual([]);
	});

	it("leaves out a plugin whose UI plugin brings its own pages", () => {
		const ui = { plugins: [plugin("gmail")] };

		expect(generatedNav(ui, [])).toHaveLength(1);
		expect(generatedNav(ui, [withNav])).toEqual([]);
	});

	it("is put in the drawer after the plugins are read, whichever list comes first", async () => {
		await loadGenerated(answering({ json: { plugins: [plugin("gmail")] } }));

		expect(plugins.getState().nav.map((entry) => entry.to)).toContain("/p/gmail/");
	});
});
