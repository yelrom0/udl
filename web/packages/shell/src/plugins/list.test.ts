/**
 * `/ui/plugins.json`, into the store: the UI plugins the page may load, and the bundles udl
 * refused. A list the page cannot read is a problem it shows, never a page that fails to open.
 */

import { expect, it, vi } from "vitest";
import { plugins } from "../stores/plugins";
import { loadPlugins } from "./list";

const GMAIL = {
	name: "gmail",
	version: "0.1.0",
	base_url: "/ui/plugins/gmail/",
	entry_url: "/ui/plugins/gmail/index.js",
	styles: ["/ui/plugins/gmail/index.css"],
	isolation: "shadow" as const,
	contributes: { nav: [{ title: "Gmail", route: "inbox" }], routes: ["inbox"] },
	plugin: "gmail",
};

/** A `fetch` that answers `body` (as JSON) with `status`. */
const answering = (body: unknown, status = 200): typeof globalThis.fetch =>
	vi.fn(async () => new Response(JSON.stringify(body), { status }));

/** A `fetch` that cannot connect. */
const failing: typeof globalThis.fetch = vi.fn(async () => {
	throw new TypeError("Failed to fetch");
});

it("puts the plugins udl lists, and its problems, in the store", async () => {
	plugins.setState({ manifests: [], problems: [], errors: [] });

	await loadPlugins(
		answering({
			plugins: [GMAIL],
			problems: [{ origin: "/tmp/ui/broken", error: "no manifest.json" }],
		}),
	);

	expect(plugins.getState().manifests).toEqual([GMAIL]);
	expect(plugins.getState().problems).toEqual([
		{ origin: "/tmp/ui/broken", error: "no manifest.json" },
	]);
});

it("asks for the list at the page's own origin", async () => {
	const fetch = answering({ plugins: [], problems: [] });

	await loadPlugins(fetch);

	expect(fetch).toHaveBeenCalledWith("/ui/plugins.json", { credentials: "same-origin" });
});

it("says the list could not be read, and leaves the page standing", async () => {
	plugins.setState({ manifests: [], problems: [], errors: [] });

	await loadPlugins(answering({ message: "nope" }, 500));

	expect(plugins.getState().manifests).toEqual([]);
	expect(plugins.getState().problems).toEqual([
		{ origin: "/ui/plugins.json", error: "udl answered 500" },
	]);
});

it("says so when udl cannot be reached at all", async () => {
	plugins.setState({ manifests: [], problems: [], errors: [] });

	await loadPlugins(failing);

	expect(plugins.getState().problems).toEqual([
		{ origin: "/ui/plugins.json", error: "Failed to fetch" },
	]);
});
