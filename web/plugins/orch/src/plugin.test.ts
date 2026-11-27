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

	it("keeps the Settings tab and the setup operations, and hides the rest (29.9)", () => {
		// (Its day-to-day operations have pages of their own; its setup ones — projects, repos,
		// machines, import — are what the generated page is for.)
		expect(manifest.generated.tabs).toEqual(["settings", "operations"]);
		expect(manifest.generated.hide).toEqual(["orch_*"]);
		expect(manifest.generated.show).toEqual([
			"orch_list_runners",
			"orch_runner_*",
			"orch_list_projects",
			"orch_get_project",
			"orch_set_project",
			"orch_set_auto_merge",
			"orch_delete_project",
			"orch_set_repo",
			"orch_delete_repo",
			"orch_import_plan",
		]);
	});

	it("names the files its bundle really has", () => {
		expect(manifest.entry).toBe("index.js");
		expect(manifest.styles).toEqual(["index.css"]);
	});
});
