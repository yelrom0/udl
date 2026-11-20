/**
 * ---------------------
 * - PluginList.test.tsx -
 * ---------------------
 *
 * The Plugins page's list of what udl's plugins have (24.6): each plugin links to the page
 * generated for it, with what that page holds — and a list the page could not read says so
 * rather than showing nothing.
 */

import { render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

// (A link without a router: this is the list, not where it goes.)
vi.mock("@tanstack/react-router", () => ({
	Link: ({ to, children, ...rest }: { to: string; children: React.ReactNode }) => (
		<a href={to} {...rest}>
			{children}
		</a>
	),
}));

import { setGenerated } from "../stores/generated";
import { addError, plugins, setPlugins } from "../stores/plugins";
import { PluginList } from "./PluginList";

beforeEach(() => {
	setGenerated(null, null);
	setPlugins([], []);
	plugins.setState({ errors: [] });
});

describe("the plugins page", () => {
	it("links every plugin to its generated page, with what it has", () => {
		setGenerated(
			{
				plugins: [
					{
						name: "gmail",
						kind: "udl.integrations",
						title: "Gmail",
						summary: "",
						has_ui_plugin: false,
						operations: [
							{
								name: "list_accounts",
								title: "Accounts",
								summary: "",
								description: "",
								hints: {
									read_only: true,
									destructive: false,
									idempotent: true,
									open_world: false,
									admin: false,
									mcp: true,
								},
								ui: {
									group: null,
									order: 0,
									view: "list",
									icon: null,
									hidden: false,
									confirm_field: null,
								},
								method: null,
								path: null,
								request_schema: {},
								response_schema: {},
							},
						],
						settings: { json_schema: {}, read_only: [], secret: [] },
						accounts: { connect: ["oauth"], access_levels: ["read"], options_schema: {} },
						event_types: ["gmail.message"],
					},
					{
						name: "quiet",
						kind: null,
						title: "Quiet",
						summary: "",
						has_ui_plugin: false,
						operations: [],
						settings: null,
						accounts: null,
						event_types: [],
					},
				],
			},
			null,
		);
		render(<PluginList />);

		const link = screen.getByRole("link", { name: "Gmail" });
		expect(link.getAttribute("href")).toBe("/p/gmail/");
		expect(screen.getByText("operations: 1, settings, accounts, events: 1")).toBeDefined();
		expect(screen.getByRole("link", { name: "Quiet" })).toBeDefined();
		expect(screen.getByText("nothing to show")).toBeDefined();
		// (Only a plugin with something to show has a page to open.)
		expect(screen.getAllByRole("link", { name: "Open" })).toHaveLength(1);
	});

	it("says what a UI plugin brings, and what udl and the browser refused", () => {
		setGenerated({ plugins: [] }, null);
		setPlugins(
			[
				{
					name: "shadcn",
					version: "0.1.0",
					base_url: "/ui/plugins/shadcn/",
					entry_url: "/ui/plugins/shadcn/index.js",
					styles: ["/ui/plugins/shadcn/index.css"],
					isolation: "shadow",
					contributes: {
						nav: [{ title: "Shadcn", route: "demo" }],
						routes: ["demo", "other"],
						widgets: [{ slot: "dashboard", id: "demo" }],
						settings: ["shadcn"],
						accountSetup: ["shadcn"],
						eventViews: ["shadcn.demo"],
						filterEditors: ["shadcn.demo"],
						actionEditors: ["shadcn.demo"],
						themes: ["shadcn"],
					},
					plugin: "shadcn",
				},
			],
			[{ origin: "/ui/plugins/bad/entry.js", error: "it is not a module" }],
		);
		addError("broken", "the import failed");
		render(<PluginList />);

		expect(screen.getByText("shadcn")).toBeDefined();
		expect(screen.getByText("nav items: 1")).toBeDefined();
		expect(screen.getByText("pages: 2")).toBeDefined();
		expect(screen.getByText("widgets: 1")).toBeDefined();
		expect(screen.getByText("themes: 1")).toBeDefined();
		expect(screen.getByText("settings: shadcn")).toBeDefined();
		expect(screen.getByText("account setup: shadcn")).toBeDefined();
		expect(screen.getByText("event views: shadcn.demo")).toBeDefined();
		expect(screen.getByText("filter editors: shadcn.demo")).toBeDefined();
		expect(screen.getByText("action editors: shadcn.demo")).toBeDefined();
		expect(screen.getByText(/it is not a module/)).toBeDefined();
		expect(screen.getByText(/the import failed/)).toBeDefined();
	});

	it("says no plugin brings a web UI", () => {
		setGenerated({ plugins: [] }, null);
		render(<PluginList />);

		expect(screen.getByText(/No plugin brings a web UI/)).toBeDefined();
	});

	it("says why it has none, when udl answered nothing usable", () => {
		setGenerated(null, "udl answered 500");

		render(<PluginList />);

		expect(screen.getByText("udl answered 500")).toBeDefined();
	});

	it("says so before udl has answered", () => {
		render(<PluginList />);

		expect(screen.getByText(/udl has not said what its plugins have to show yet/)).toBeDefined();
	});
});
