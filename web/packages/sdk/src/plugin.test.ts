/**
 * -----------------
 * - plugin.test.ts -
 * -----------------
 *
 * What a UI plugin is: `defineUIPlugin` keeps its spec as it was given (and hands it to the
 * shell unchanged), and `version` is the SDK's own — the one a bundle's `sdk` range is checked
 * against. The types (a `Mount` per contribution point, `UIContext`) are checked by `tsc`.
 */

import { describe, expect, it } from "vitest";
import { defineUIPlugin, version } from "./index";

describe("a UI plugin's spec", () => {
	it("is what defineUIPlugin was given", () => {
		const routes = { inbox: () => ({ update: () => {}, unmount: () => {} }) };

		const plugin = defineUIPlugin({ routes });

		expect(plugin.routes).toBe(routes);
	});

	it("keeps every contribution point", () => {
		const mount = () => ({ update: () => {}, unmount: () => {} });

		const plugin = defineUIPlugin({
			routes: { inbox: mount },
			settings: { gmail: mount },
			accountSetup: { gmail: mount },
			eventViews: { "gmail.*": mount },
			filterEditors: { gmail: mount },
			actionEditors: { slack: mount },
			widgets: { unread: mount },
		});

		expect(Object.keys(plugin).sort()).toEqual([
			"accountSetup",
			"actionEditors",
			"eventViews",
			"filterEditors",
			"routes",
			"settings",
			"widgets",
		]);
	});

	it("may be empty (a plugin that only brings themes)", () => {
		expect(defineUIPlugin({})).toEqual({});
	});
});

describe("the SDK's version", () => {
	it("is MAJOR.MINOR.PATCH, so a manifest's ^MAJOR.MINOR can be checked against it", () => {
		expect(version).toMatch(/^\d+\.\d+\.\d+$/);
	});

	it("is the package's (a bundle is built against what it says)", async () => {
		const packageJson = (await import("../package.json")) as { version: string };

		expect(packageJson.version).toBe(version);
	});
});
