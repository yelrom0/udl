/**
 * Where a contribution is drawn: its own shadow root (or the shell's DOM), the plugin's
 * stylesheets adopted there, its `@property` rules hoisted to the document (a shadow root
 * ignores them), and a wrapper carrying the theme.
 */

import { beforeEach, describe, expect, it, vi } from "vitest";
import type { UIPluginInfo } from "../stores/plugins";
import { cssOf } from "../test-setup";
import { clearHoisted, prepare } from "./dom";

const GMAIL: UIPluginInfo = {
	name: "gmail",
	version: "0.1.0",
	base_url: "/ui/plugins/gmail/",
	entry_url: "/ui/plugins/gmail/index.js",
	styles: ["/ui/plugins/gmail/index.css"],
	isolation: "shadow",
	contributes: {},
	plugin: "gmail",
};

const TAILWIND = `.card { color: rgb(1, 2, 3); }
@property --angle { syntax: "<angle>"; inherits: false; initial-value: 0deg; }`;

/** A `fetch` that answers each stylesheet's text. */
const serving = (text = TAILWIND): typeof globalThis.fetch =>
	vi.fn(async () => new Response(text, { status: 200 }));

beforeEach(clearHoisted);

describe("preparing where a plugin draws", () => {
	it("gives a shadow plugin an open shadow root, with its stylesheet adopted", async () => {
		const host = document.createElement("div");

		const point = await prepare(host, GMAIL, { theme: "dark", fetch: serving() });

		expect(host.shadowRoot?.mode).toBe("open");
		const sheets = host.shadowRoot?.adoptedStyleSheets ?? [];
		expect(sheets.map(cssOf)).toHaveLength(1);
		expect(sheets.map(cssOf)[0]).toContain("rgb(1, 2, 3)");
		expect(host.shadowRoot?.contains(point.target)).toBe(true);
	});

	it("gives the wrapper the theme, and draws the plugin inside it", async () => {
		const host = document.createElement("div");

		const point = await prepare(host, GMAIL, { theme: "dark", fetch: serving() });

		expect(point.wrapper.dataset.theme).toBe("dark");
		expect(point.wrapper.contains(point.target)).toBe(true);
		expect(point.target).not.toBe(host);
	});

	it("leaves a plugin that asks for no isolation in the shell's own DOM", async () => {
		const host = document.createElement("div");

		const point = await prepare(
			host,
			{ ...GMAIL, isolation: "none" },
			{ theme: "light", fetch: serving() },
		);

		expect(host.shadowRoot).toBeNull();
		expect(point.wrapper.parentElement).toBe(host);
		expect(point.target.parentElement).toBe(point.wrapper);
		expect(point.wrapper.dataset.theme).toBe("light");
	});

	it("hoists a plugin's @property rules to one document-level sheet, once", async () => {
		await prepare(document.createElement("div"), GMAIL, { theme: "dark", fetch: serving() });
		await prepare(document.createElement("div"), GMAIL, { theme: "dark", fetch: serving() });

		const hoisted = document.adoptedStyleSheets
			.map(cssOf)
			.filter((css) => css.includes("@property"));
		expect(hoisted).toEqual([
			'@property --angle { syntax: "<angle>"; inherits: false; initial-value: 0deg; }',
		]);
	});

	it("hoists a second stylesheet's @property rules to the same plugin's sheet", async () => {
		const two = [
			'@property --angle { syntax: "<angle>"; inherits: false; initial-value: 0deg; }',
			'@property --tint { syntax: "<color>"; inherits: false; initial-value: red; }',
		];
		const fetch = vi.fn(
			async (input: RequestInfo | URL) =>
				new Response(String(input).endsWith("two.css") ? two[1] : two[0], { status: 200 }),
		);

		await prepare(
			document.createElement("div"),
			{ ...GMAIL, styles: ["/ui/plugins/gmail/one.css"] },
			{ theme: "dark", fetch },
		);
		await prepare(
			document.createElement("div"),
			{ ...GMAIL, styles: ["/ui/plugins/gmail/two.css"] },
			{ theme: "dark", fetch },
		);

		const sheets = document.adoptedStyleSheets
			.map(cssOf)
			.filter((css) => css.includes("@property"));
		expect(sheets).toHaveLength(1);
		expect(sheets[0]).toContain("--angle");
		expect(sheets[0]).toContain("--tint");
	});

	it("hoists nothing when a plugin has no @property rules", async () => {
		await prepare(document.createElement("div"), GMAIL, {
			theme: "dark",
			fetch: serving(".card { color: red; }"),
		});

		expect(document.adoptedStyleSheets).toHaveLength(0);
	});

	it("says which stylesheet could not be read", async () => {
		const fetch = vi.fn(async () => new Response("", { status: 404 }));

		await expect(
			prepare(document.createElement("div"), GMAIL, { theme: "dark", fetch }),
		).rejects.toThrow("udl could not read /ui/plugins/gmail/index.css: it answered 404");
	});
});
