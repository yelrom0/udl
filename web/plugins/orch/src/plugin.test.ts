import { describe, expect, it } from "vitest";
import manifest from "../manifest.json";
import plugin from "./index";

describe("the orchestrator's manifest and its entry", () => {
	it("names a mount for every route the manifest contributes", () => {
		expect(Object.keys(plugin.routes ?? {}).sort()).toEqual(
			[...manifest.contributes.routes].sort(),
		);
	});

	it("names a mount for every widget the manifest contributes", () => {
		const declared = manifest.contributes.widgets.map((widget) => widget.id);
		expect(Object.keys(plugin.widgets ?? {}).sort()).toEqual([...declared].sort());
	});

	it("gives every nav item a route the plugin has", () => {
		const routes = Object.keys(plugin.routes ?? {});
		for (const item of manifest.contributes.nav) {
			expect(routes).toContain(item.route);
		}
	});

	it("puts Progress after Board in the nav (Q-8)", () => {
		const titles = manifest.contributes.nav.map((item) => item.title);

		expect(titles.indexOf("Progress")).toBe(titles.indexOf("Board") + 1);
	});

	it("keeps only the generated page's Settings tab, and hides every orch operation", () => {
		expect(manifest.generated.tabs).toEqual(["settings"]);
		expect(manifest.generated.hide).toEqual(["orch_*"]);
	});

	it("names the files its bundle really has", () => {
		expect(manifest.entry).toBe("index.js");
		expect(manifest.styles).toEqual(["index.css"]);
	});
});
