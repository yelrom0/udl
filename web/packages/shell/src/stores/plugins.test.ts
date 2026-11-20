import { renderHook } from "@testing-library/react";
import { act } from "react";
import { beforeEach, describe, expect, it } from "vitest";
import { addError, plugins, setPlugins, usePluginErrors, usePlugins } from "./plugins";

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

beforeEach(() => {
	plugins.setState({ manifests: [], problems: [], errors: [] });
});

describe("the plugins store", () => {
	it("knows no plugins at first", () => {
		expect(plugins.getState().manifests).toEqual([]);
		expect(plugins.getState().problems).toEqual([]);
		expect(plugins.getState().errors).toEqual([]);
	});

	it("keeps what udl listed, and the bundles it refused", () => {
		setPlugins([GMAIL], [{ origin: "/tmp/ui/broken", error: "no manifest.json" }]);

		expect(plugins.getState().manifests).toEqual([GMAIL]);
		expect(plugins.getState().manifests[0]?.contributes?.nav?.[0]?.title).toBe("Gmail");
		expect(plugins.getState().problems).toEqual([
			{ origin: "/tmp/ui/broken", error: "no manifest.json" },
		]);
	});

	it("replaces the list rather than adding to it", () => {
		setPlugins([GMAIL], []);
		setPlugins([], []);

		expect(plugins.getState().manifests).toEqual([]);
	});

	it("lists a bundle that would not load, and keeps the others", () => {
		setPlugins([GMAIL], []);

		addError("gmail", "its bundle 404s");

		expect(plugins.getState().errors).toEqual([{ name: "gmail", message: "its bundle 404s" }]);
		expect(plugins.getState().manifests).toHaveLength(1);
	});

	it("says a bundle failed once, however often it is asked", () => {
		setPlugins([GMAIL], []);

		addError("gmail", "its bundle 404s");
		addError("gmail", "its bundle 404s");

		expect(plugins.getState().errors).toHaveLength(1);
	});

	it("hands the manifests and the errors to a component", () => {
		const manifests = renderHook(() => usePlugins());
		const errors = renderHook(() => usePluginErrors());

		act(() => {
			setPlugins([GMAIL], []);
			addError("gmail", "its bundle 404s");
		});

		expect(manifests.result.current).toEqual([GMAIL]);
		expect(errors.result.current).toEqual([{ name: "gmail", message: "its bundle 404s" }]);
	});
});
