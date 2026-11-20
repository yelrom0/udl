/**
 * Loading a UI plugin's bundle: its entry's ES module, imported once however many of its
 * contributions the page draws, and remembered when it fails too (a broken bundle is not asked
 * again on every route change).
 */

import { beforeEach, describe, expect, it, vi } from "vitest";
import type { UIPluginInfo } from "../stores/plugins";
import { forgetPlugins, loadPlugin } from "./load";

const GMAIL: UIPluginInfo = {
	name: "gmail",
	version: "0.1.0",
	base_url: "/ui/plugins/gmail/",
	entry_url: "/ui/plugins/gmail/index.js",
	styles: [],
	isolation: "shadow",
	contributes: { routes: ["inbox"] },
	plugin: "gmail",
};

const spec = { routes: { inbox: () => ({ update: () => {}, unmount: () => {} }) } };

beforeEach(forgetPlugins);

describe("loading a plugin", () => {
	it("imports the entry the manifest names", async () => {
		const importer = vi.fn(async () => ({ default: spec }));

		const loaded = await loadPlugin(GMAIL, importer);

		expect(importer).toHaveBeenCalledWith("/ui/plugins/gmail/index.js");
		expect(loaded).toBe(spec);
	});

	it("imports a bundle once, however many contributions are drawn", async () => {
		const importer = vi.fn(async () => ({ default: spec }));

		const [first, second] = await Promise.all([
			loadPlugin(GMAIL, importer),
			loadPlugin(GMAIL, importer),
		]);

		expect(importer).toHaveBeenCalledTimes(1);
		expect(first).toBe(second);
	});

	it("remembers a bundle that failed, so it is not asked again", async () => {
		const importer = vi.fn(async () => {
			throw new TypeError("Failed to fetch dynamically imported module");
		});

		await expect(loadPlugin(GMAIL, importer)).rejects.toThrow("Failed to fetch");
		await expect(loadPlugin(GMAIL, importer)).rejects.toThrow("Failed to fetch");
		expect(importer).toHaveBeenCalledTimes(1);
	});

	it("refuses a module that exports no plugin", async () => {
		const importer = vi.fn(async () => ({ routes: spec.routes }));

		await expect(loadPlugin(GMAIL, importer)).rejects.toThrow(
			"/ui/plugins/gmail/index.js exports no plugin: its default export is what the shell mounts",
		);
	});

	it("imports again once they are forgotten", async () => {
		const importer = vi.fn(async () => ({ default: spec }));

		await loadPlugin(GMAIL, importer);
		forgetPlugins();
		await loadPlugin(GMAIL, importer);

		expect(importer).toHaveBeenCalledTimes(2);
	});
});
