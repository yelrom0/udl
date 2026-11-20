/**
 * The template's own guard: what its manifest says it contributes is what its entry has. A
 * starting point that drifts (a route added to one and not the other) is a plugin the shell
 * cannot load, so it is a test here rather than a surprise there.
 */

import { describe, expect, it } from "vitest";
import manifest from "../manifest.json";
import plugin from "./index";

describe("the template's manifest and its entry", () => {
	it("names a mount for every route the manifest contributes", () => {
		expect(Object.keys(plugin.routes ?? {}).sort()).toEqual(
			[...(manifest.contributes.routes ?? [])].sort(),
		);
	});

	it("names a mount for every widget the manifest contributes", () => {
		const declared = (manifest.contributes.widgets ?? []).map((widget) => widget.id);
		expect(Object.keys(plugin.widgets ?? {}).sort()).toEqual([...declared].sort());
	});

	it("gives every nav item a route the plugin has", () => {
		const routes = Object.keys(plugin.routes ?? {});
		for (const item of manifest.contributes.nav ?? []) {
			expect(routes).toContain(item.route);
		}
	});

	it("names the files its bundle really has", () => {
		expect(manifest.entry).toBe("index.js");
		expect(manifest.styles).toEqual(["index.css"]);
	});
});
