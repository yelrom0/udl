/**
 * What the Plugins page lists: the UI plugins the page can load, what each brings, the bundles
 * udl refused, and the ones this browser could not load.
 */

import { render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it } from "vitest";
import { addError, plugins, setPlugins, type UIPluginInfo } from "../stores/plugins";
import { PluginList } from "./PluginList";

const GMAIL: UIPluginInfo = {
	name: "gmail",
	version: "0.1.0",
	base_url: "/ui/plugins/gmail/",
	entry_url: "/ui/plugins/gmail/index.js",
	styles: [],
	isolation: "shadow",
	contributes: {
		nav: [{ title: "Gmail", route: "inbox" }],
		routes: ["inbox", "compose"],
		settings: ["gmail"],
		accountSetup: ["gmail"],
		eventViews: ["gmail.*"],
		filterEditors: ["gmail"],
		actionEditors: ["gmail.send"],
		widgets: [{ slot: "dashboard", id: "unread" }],
		themes: ["gmail-dark"],
	},
	plugin: "gmail",
};

beforeEach(() => {
	plugins.setState({ manifests: [], problems: [], errors: [], nav: [] });
});

describe("the plugins list", () => {
	it("says no plugin brings a web UI", () => {
		render(<PluginList />);

		expect(screen.getByText(/No plugin brings a web UI/)).toBeTruthy();
	});

	it("lists a plugin, with what it adds", () => {
		setPlugins([GMAIL], []);

		render(<PluginList />);

		expect(screen.getByText("gmail")).toBeTruthy();
		expect(screen.getByText("0.1.0")).toBeTruthy();
		expect(screen.getByText("shadow")).toBeTruthy();
		expect(screen.getByText("nav items: 1")).toBeTruthy();
		expect(screen.getByText("pages: 2")).toBeTruthy();
		expect(screen.getByText("widgets: 1")).toBeTruthy();
		expect(screen.getByText("themes: 1")).toBeTruthy();
		expect(screen.getByText("settings: gmail")).toBeTruthy();
		expect(screen.getByText("account setup: gmail")).toBeTruthy();
		expect(screen.getByText("event views: gmail.*")).toBeTruthy();
		expect(screen.getByText("filter editors: gmail")).toBeTruthy();
		expect(screen.getByText("action editors: gmail.send")).toBeTruthy();
	});

	it("lists a plugin that adds nothing beyond its page", () => {
		setPlugins([{ ...GMAIL, contributes: {} }], []);

		render(<PluginList />);

		expect(screen.getByText("gmail")).toBeTruthy();
		expect(screen.queryByText(/nav items/)).toBeNull();
	});

	it("lists the bundles udl refused, and why", () => {
		setPlugins([], [{ origin: "/tmp/ui/broken", error: "no manifest.json" }]);

		render(<PluginList />);

		expect(screen.getByText("Refused bundles")).toBeTruthy();
		expect(screen.getByText("/tmp/ui/broken")).toBeTruthy();
		expect(screen.getByText(/no manifest\.json/)).toBeTruthy();
	});

	it("lists the bundles this browser could not load", () => {
		setPlugins([GMAIL], []);
		addError("gmail", "its bundle 404s");

		render(<PluginList />);

		expect(screen.getByText("Bundles this browser could not load")).toBeTruthy();
		expect(screen.getByText(/its bundle 404s/)).toBeTruthy();
	});
});
