/**
 * What fills the page's registry: the shell's own pages and widgets at startup, and each UI
 * plugin udl lists, from its manifest (no bundle is imported until one is drawn).
 */

import { beforeEach, describe, expect, it } from "vitest";
import { plugins, type UIPluginInfo } from "../stores/plugins";
import { OWN_NAV, registerOwn, registerPlugin, registerPlugins, shellRegistry, UDL } from "./shell";

const GMAIL: UIPluginInfo = {
	name: "gmail",
	version: "0.1.0",
	base_url: "/ui/plugins/gmail/",
	entry_url: "/ui/plugins/gmail/index.js",
	styles: ["/ui/plugins/gmail/index.css"],
	isolation: "shadow",
	contributes: {
		nav: [
			{ title: "Gmail", route: "inbox", icon: "mail" },
			{ title: "Sent", route: "sent" },
		],
		routes: ["inbox", "message/$id"],
		settings: ["gmail"],
		accountSetup: ["gmail"],
		eventViews: ["gmail.*"],
		filterEditors: ["gmail"],
		actionEditors: ["gmail.send"],
		widgets: [{ slot: "dashboard", id: "unread" }],
	},
	plugin: "gmail",
};

beforeEach(() => {
	shellRegistry.clear();
	plugins.setState({ manifests: [], problems: [], errors: [], nav: [] });
});

describe("registering the shell's own pages", () => {
	it("puts them in the navigation, as the shell's own", () => {
		registerOwn(shellRegistry);

		expect(shellRegistry.nav).toHaveLength(OWN_NAV.length);
		expect(shellRegistry.nav[0]).toEqual({ plugin: UDL, title: "Dashboard", to: "/" });
	});

	it("does not put them in twice", () => {
		registerOwn(shellRegistry);
		registerOwn(shellRegistry);

		expect(shellRegistry.nav).toHaveLength(OWN_NAV.length);
	});
});

describe("registering a plugin", () => {
	it("puts its nav items in the navigation, under its own page", () => {
		registerPlugin(GMAIL);

		expect(shellRegistry.nav.map((item) => item.to)).toEqual(["/p/gmail/inbox", "/p/gmail/sent"]);
		expect(shellRegistry.nav[0]).toMatchObject({ title: "Gmail", icon: "mail" });
		expect(shellRegistry.nav[1]?.icon).toBeUndefined();
	});

	it("puts each of its contributions under the key its point uses", () => {
		registerPlugin(GMAIL);

		expect(shellRegistry.point("routes").at("inbox")).toHaveLength(1);
		expect(shellRegistry.point("routes").at("message/$id")).toHaveLength(1);
		expect(shellRegistry.point("settings").at("gmail")).toHaveLength(1);
		expect(shellRegistry.point("accountSetup").at("gmail")).toHaveLength(1);
		expect(shellRegistry.point("eventViews").at("gmail.*")).toHaveLength(1);
		expect(shellRegistry.point("filterEditors").at("gmail")).toHaveLength(1);
		expect(shellRegistry.point("actionEditors").at("gmail.send")).toHaveLength(1);
	});

	it("puts a widget under its slot, with the id its bundle has it by", () => {
		registerPlugin(GMAIL);

		const [widget] = shellRegistry.point("widgets").at("dashboard");
		expect(widget?.id).toBe("unread");
		expect(widget?.entry).toBe("unread");
		expect(widget?.mount).toBeUndefined();
	});

	it("draws nothing itself: the mount comes from the bundle when it is drawn", () => {
		registerPlugin(GMAIL);

		const [route] = shellRegistry.point("routes").at("inbox");
		expect(route?.mount).toBeUndefined();
		expect(route?.plugin).toEqual({
			name: "gmail",
			version: "0.1.0",
			baseUrl: "/ui/plugins/gmail/",
		});
	});
});

describe("registering the plugins udl lists", () => {
	it("replaces what the page knew, and keeps the shell's own pages", () => {
		registerPlugins([GMAIL]);
		expect(shellRegistry.nav).toHaveLength(OWN_NAV.length + 2);

		registerPlugins([]);

		expect(shellRegistry.nav).toHaveLength(OWN_NAV.length);
		expect(shellRegistry.point("routes").at("inbox")).toEqual([]);
	});

	it("says what the navigation is now, for the page to draw", () => {
		registerPlugins([GMAIL]);

		expect(plugins.getState().nav).toEqual(shellRegistry.nav);
		expect(plugins.getState().nav.map((item) => item.title)).toContain("Gmail");
	});
});
