/**
 * --------------------
 * - PluginPage.test.tsx -
 * --------------------
 *
 * A plugin's generated page, against `FakeUdl` (24.6's Done-when): a read-only operation's table
 * on open, a destructive one only after confirming, a setting changed, an account connected,
 * checked and disconnected, and a plugin with no UI plugin of its own.
 */

import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import type { components, UdlApi } from "@udl/api";
import { FakeUdl } from "@udl/api/testing";
import type { JsonObject } from "@udl/ui-sdk";
import { beforeEach, describe, expect, it } from "vitest";
import { ConfirmHost } from "../components/ConfirmHost";
import { confirms } from "../stores/confirms";
import { generated, setGenerated } from "../stores/generated";
import { PluginPage } from "./PluginPage";
import type { GeneratedOperation, GeneratedPlugin, GeneratedUISpec } from "./spec";

/** An account, as the API's schema holds one. */
type Account = components["schemas"]["AccountInfo"];

const HINTS = {
	read_only: true,
	destructive: false,
	idempotent: true,
	open_world: false,
	admin: false,
	mcp: true,
};

const UI: GeneratedUISpec = {
	group: null,
	order: 0,
	view: "list",
	icon: null,
	hidden: false,
	confirm_field: null,
};

/** A site as udl lists one. */
function site(key: string): components["schemas"]["Site"] {
	return { key, name: key };
}

/** An account as udl lists one. */
function account(integration: string, name: string): Account {
	return {
		id: `3f1b6c62-0000-4000-8000-${name.padEnd(12, "0").slice(0, 12)}`,
		account: `${integration}:${name}`,
		integration,
		name,
		method: "tokens",
		status: "connected",
		enabled: true,
		access: ["read"],
		options: {},
		pending_until: null,
		last_error: null,
		created_at: "2026-01-01T00:00:00Z",
		connected_at: "2026-01-01T00:00:00Z",
		identity: { id: "1", display: name },
	};
}

/** A schema for a request of one string field. */
function formSchema(field: string, required = true): JsonObject {
	return {
		type: "object",
		title: "Request",
		properties: { [field]: { type: "string", title: field } },
		required: required ? [field] : [],
	};
}

/** A schema for a response that is a list of objects with one field. */
function tableSchema(field: string): JsonObject {
	return {
		type: "array",
		title: "Answer",
		items: { type: "object", properties: { name: { type: "string", title: field } } },
	};
}

function operation(name: string, overrides: Partial<GeneratedOperation> = {}): GeneratedOperation {
	return {
		name,
		title: name,
		summary: "",
		description: "",
		hints: HINTS,
		ui: UI,
		method: null,
		path: null,
		request_schema: {},
		response_schema: {},
		...overrides,
	};
}

function plugin(overrides: Partial<GeneratedPlugin> = {}): GeneratedPlugin {
	return {
		name: "fakes",
		kind: "udl.integrations",
		title: "Fakes",
		summary: "",
		has_ui_plugin: false,
		operations: [],
		settings: null,
		accounts: null,
		event_types: [],
		...overrides,
	};
}

/** Draw `one`'s page. `api` replaces the fake's client for a plugin operation's own route,
 * which the API's schema does not hold. */
function draw(one: GeneratedPlugin, fake = new FakeUdl(), api?: UdlApi) {
	setGenerated({ plugins: [one] }, null);
	render(
		<>
			<PluginPage plugin={one} api={api ?? fake.api()} />
			<ConfirmHost />
		</>,
	);
	return fake;
}

beforeEach(() => {
	generated.setState({ ui: null, error: null });
	// (A question left open by a test is not the next test's.)
	confirms.setState({ asked: [] });
});

describe("a plugin's generated page", () => {
	it("runs a list on open, and shows its table", async () => {
		const fake = new FakeUdl();
		const asked: { method: string; path: string }[] = [];
		const api: UdlApi = {
			...fake.api(),
			callAt: (method, path) => {
				asked.push({ method, path });
				return Promise.resolve([{ name: "one" }, { name: "two" }]);
			},
		};
		draw(
			plugin({
				operations: [
					operation("fake_list_things", {
						title: "List the things",
						method: "POST",
						path: "/integrations/fake/list_things",
						request_schema: {},
						response_schema: tableSchema("fakes"),
					}),
				],
			}),
			fake,
			api,
		);

		expect(await screen.findByText("one")).toBeDefined();
		expect(screen.getByText("two")).toBeDefined();
		// (It ran itself, once.)
		await waitFor(() => expect(asked).toHaveLength(1));
		expect(asked[0]).toEqual({ method: "POST", path: "/integrations/fake/list_things" });
	});

	it("sends a fan-out list's account with the rest of its values", async () => {
		const fake = new FakeUdl();
		const sent: unknown[] = [];
		// (The plugin's own route: the page calls it by its path, with the form's values.)
		const api: UdlApi = {
			...fake.api(),
			callAt: (method, path, body) => {
				sent.push({ method, path, body });
				return Promise.resolve({ items: [] });
			},
		};
		draw(
			plugin({
				name: "fake",
				title: "Fake",
				operations: [
					operation("fake_list_things", {
						title: "List the things",
						method: "POST",
						path: "/integrations/fake/list_things",
						request_schema: {
							type: "object",
							properties: {
								// (One or many, or none: a real union, so a box of JSON.)
								account: {
									anyOf: [
										{ type: "string" },
										{ type: "array", items: { type: "string" } },
										{ type: "null" },
									],
									title: "Account",
								},
								contains: { type: "string", title: "Contains", default: "" },
							},
						},
					}),
				],
			}),
			fake,
			api,
		);

		// (Its run on open has answered before the form is filled and run again.)
		await waitFor(() => expect(sent).toHaveLength(1));
		fireEvent.change(screen.getByLabelText(/^Account/), {
			target: { value: '"fake:work"' },
		});
		fireEvent.click(screen.getByRole("button", { name: "Refresh" }));

		// (It ran once on open with no account, and again with what was typed.)
		await waitFor(() => expect(sent).toHaveLength(2));
		expect(sent[1]).toEqual({
			method: "POST",
			path: "/integrations/fake/list_things",
			body: { account: "fake:work" },
		});
	});

	it("refreshes a list when its button is pressed", async () => {
		const fake = new FakeUdl();
		let asked = 0;
		const api: UdlApi = {
			...fake.api(),
			callAt: () => {
				asked += 1;
				return Promise.resolve([{ name: `${asked}` }]);
			},
		};
		draw(
			plugin({
				operations: [
					operation("fake_list_things", {
						method: "POST",
						path: "/integrations/fake/list_things",
						response_schema: tableSchema("fakes"),
					}),
				],
			}),
			fake,
			api,
		);

		await screen.findByText("1");
		fireEvent.click(screen.getByRole("button", { name: "Refresh" }));

		expect(await screen.findByText("2")).toBeDefined();
	});

	it("asks before a destructive operation, and runs it only when told to", async () => {
		const fake = draw(
			plugin({
				operations: [
					operation("check_account", {
						title: "Delete fake",
						hints: { ...HINTS, read_only: false, destructive: true },
						ui: { ...UI, view: "form" },
						request_schema: formSchema("account"),
					}),
				],
			}),
		);
		fake.handle("check_account", () => ({ ok: true, problems: [] }));

		fireEvent.change(screen.getByLabelText(/^account/), { target: { value: "gmail:work" } });
		fireEvent.click(screen.getByRole("button", { name: "Run" }));

		const dialog = await screen.findByRole("dialog");
		expect(within(dialog).getByText(/cannot be undone/)).toBeDefined();
		expect(fake.calls.filter((call) => call.id === "check_account")).toHaveLength(0);

		fireEvent.click(within(dialog).getByRole("button", { name: "Delete fake" }));

		await waitFor(() =>
			expect(fake.calls.filter((call) => call.id === "check_account")).toHaveLength(1),
		);
	});

	it("does nothing when the question is answered no", async () => {
		const fake = draw(
			plugin({
				operations: [
					operation("check_account", {
						title: "Check account",
						hints: { ...HINTS, read_only: false, destructive: true },
						ui: { ...UI, view: "action" },
					}),
				],
			}),
		);
		fake.handle("check_account", () => ({ ok: true, problems: [] }));

		fireEvent.click(screen.getByRole("button", { name: "Run" }));
		const dialog = await screen.findByRole("dialog");
		fireEvent.click(within(dialog).getByRole("button", { name: "Cancel" }));

		expect(fake.calls.filter((call) => call.id === "check_account")).toHaveLength(0);
	});

	it("says what to type when the confirm field repeats no name", async () => {
		draw(
			plugin({
				operations: [
					operation("list_sites", {
						ui: { ...UI, view: "form", confirm_field: "confirm" },
						request_schema: {
							type: "object",
							properties: { confirm: { type: "string", title: "confirm" } },
						},
					}),
				],
			}),
		);

		expect(screen.getByText("Type anything in confirm to confirm.")).toBeDefined();
	});

	it("names the thing it is about to change in the question", async () => {
		const fake = draw(
			plugin({
				operations: [
					operation("check_account", {
						title: "Drop account",
						hints: { ...HINTS, read_only: false, destructive: true },
						ui: { ...UI, view: "form", confirm_field: "confirm" },
						request_schema: {
							type: "object",
							properties: {
								account: { type: "string", title: "account" },
								confirm: { type: "string", title: "confirm" },
							},
							required: ["account", "confirm"],
						},
					}),
				],
			}),
		);
		fake.handle("check_account", () => ({ ok: true, problems: [] }));

		fireEvent.change(screen.getByLabelText(/^account/), {
			target: { value: "gmail:work" },
		});
		expect(screen.getByText("Type gmail:work in confirm to confirm.")).toBeDefined();
		fireEvent.change(screen.getByLabelText(/^confirm/), { target: { value: "gmail:work" } });
		fireEvent.click(screen.getByRole("button", { name: "Run" }));

		const dialog = await screen.findByRole("dialog");
		expect(dialog.textContent).toContain("Drop account gmail:work?");
	});

	it("keeps a confirm field's button disabled until it matches", async () => {
		draw(
			plugin({
				operations: [
					operation("check_account", {
						title: "Drop fake",
						hints: { ...HINTS, read_only: false, destructive: true },
						ui: { ...UI, view: "form", confirm_field: "confirm" },
						request_schema: {
							type: "object",
							properties: {
								account: { type: "string", title: "account" },
								confirm: { type: "string", title: "confirm" },
							},
							required: ["account", "confirm"],
						},
					}),
				],
			}),
		);

		const button = screen.getByRole("button", { name: "Run" });
		fireEvent.change(screen.getByLabelText(/^account/), { target: { value: "gmail:work" } });
		expect(button.hasAttribute("disabled")).toBe(true);
		fireEvent.change(screen.getByLabelText(/^confirm/), { target: { value: "gmail:wor" } });
		expect(button.hasAttribute("disabled")).toBe(true);
		fireEvent.change(screen.getByLabelText(/^confirm/), { target: { value: "gmail:work" } });
		expect(button.hasAttribute("disabled")).toBe(false);
	});

	it("says what udl said about an operation it refused, its hint too", async () => {
		const fake = draw(
			plugin({
				operations: [
					operation("check_account", {
						ui: { ...UI, view: "action" },
					}),
				],
			}),
		);
		fake.handle("check_account", () => {
			throw new Error("it refused");
		});

		fireEvent.click(screen.getByRole("button", { name: "Run" }));

		expect(await screen.findByRole("alert")).toBeDefined();
	});

	it("says a refusal's hint under its message", async () => {
		const fake = draw(
			plugin({
				operations: [
					operation("check_account", {
						ui: { ...UI, view: "form" },
						request_schema: formSchema("account"),
					}),
				],
			}),
		);
		fake.handle("check_account", () => {
			throw Object.assign(new Error("no"), {
				info: { message: "it refused", hint: "connect an account first" },
			});
		});

		fireEvent.change(screen.getByLabelText(/^account/), { target: { value: "gmail:work" } });
		fireEvent.click(screen.getByRole("button", { name: "Run" }));

		const alert = await screen.findByRole("alert");
		expect(alert.textContent).toContain("it refused");
		expect(alert.textContent).toContain("connect an account first");
	});

	it("changes a setting, and does not draw a secret back", async () => {
		const fake = draw(
			plugin({
				settings: {
					json_schema: {
						type: "object",
						properties: { api_key: { type: "string", title: "api key" } },
					},
					read_only: [],
					secret: ["api_key"],
				},
			}),
		);
		fake.handle("get_plugin_settings", () => ({
			name: "fakes",
			kind: "udl.integrations",
			json_schema: {
				type: "object",
				properties: { api_key: { type: "string", title: "api key" } },
			},
			values: {},
			sources: {},
			file_only: [],
			secrets: ["api_key"],
		}));
		let saved: unknown = null;
		fake.handle("set_plugin_settings", ({ body }) => {
			saved = body;
			return {
				name: "fakes",
				kind: "udl.integrations",
				json_schema: { type: "object", properties: {} },
				values: {},
				sources: {},
				file_only: [],
				secrets: ["api_key"],
			};
		});

		fireEvent.click(screen.getByRole("tab", { name: "settings" }));
		const field = await screen.findByLabelText("api key");
		expect(field.getAttribute("type")).toBe("password");
		fireEvent.change(field, { target: { value: "s3cret" } });
		fireEvent.click(screen.getByRole("button", { name: "Save" }));

		expect(await screen.findByRole("status")).toBeDefined();
		expect(saved).toEqual({ values: { api_key: "s3cret" } });
	});

	it("connects an account, checks it, and disconnects it", async () => {
		const fake = draw(
			plugin({
				name: "gmail",
				title: "Gmail",
				accounts: { connect: ["tokens"], access_levels: ["read"], options_schema: {} },
				event_types: [],
			}),
		);
		let accounts: Account[] = [];
		fake.handle("list_accounts", () => ({ accounts }));
		fake.handle("connect_account", () => {
			const one = account("gmail", "work");
			accounts = [one];
			return { account: one, authorization_url: null };
		});
		fake.handle("check_account", () => ({ account: "gmail:work", ok: true, problems: [] }));
		fake.handle("disconnect_account", () => {
			accounts = [];
			return { deleted: true, revoked: null, subscriptions: [] };
		});

		fireEvent.click(screen.getByRole("tab", { name: "accounts" }));
		expect(await screen.findByText("No accounts yet.")).toBeDefined();

		fireEvent.change(screen.getByLabelText("Name"), { target: { value: "work" } });
		fireEvent.click(screen.getByRole("button", { name: "Add token" }));
		fireEvent.click(screen.getByRole("button", { name: "Connect" }));

		expect(await screen.findByText("gmail:work")).toBeDefined();

		fireEvent.click(screen.getByRole("button", { name: "Check" }));
		expect(await screen.findByText("ok")).toBeDefined();

		fireEvent.click(screen.getByRole("button", { name: "Disconnect" }));
		const dialog = await screen.findByRole("dialog");
		fireEvent.click(within(dialog).getByRole("button", { name: "Disconnect" }));

		expect(await screen.findByText("No accounts yet.")).toBeDefined();
	});

	it("lists the event types a plugin publishes", async () => {
		const fake = draw(plugin({ name: "gmail", title: "Gmail", event_types: ["gmail.message"] }));
		fake.handle("list_event_types", () => ({
			types: [
				{
					name: "gmail.message",
					summary: "A message arrived",
					source: "gmail",
					fields: [],
					json_schema: {},
					example: {},
				},
				{
					name: "sites.changed",
					summary: "Not gmail's",
					source: "udl",
					fields: [],
					json_schema: {},
					example: {},
				},
			],
		}));

		fireEvent.click(screen.getByRole("tab", { name: "events" }));

		expect(await screen.findByText("gmail.message")).toBeDefined();
		expect(screen.queryByText("sites.changed")).toBeNull();
	});

	it("draws nothing when the page closes while an operation runs", async () => {
		const fake = new FakeUdl();
		const waiting: { finish?: (value: components["schemas"]["SiteList"]) => void } = {};
		fake.handle(
			"list_sites",
			() =>
				new Promise<components["schemas"]["SiteList"]>((settle) => {
					waiting.finish = settle;
				}),
		);
		const view = render(
			<PluginPage plugin={plugin({ operations: [operation("list_sites")] })} api={fake.api()} />,
		);
		view.unmount();
		waiting.finish?.({ sites: [site("late")] });

		// (Nothing was drawn after the page closed, and nothing threw.)
		await waitFor(() => expect(fake.calls).toHaveLength(1));
	});

	it("says a plugin has nothing to show", () => {
		draw(plugin());

		expect(screen.getByText("This plugin has nothing to show.")).toBeDefined();
	});

	it("shows only the tabs a plugin has something for", () => {
		draw(plugin({ operations: [operation("list_sites")], event_types: ["fakes.changed"] }));

		expect(screen.getAllByRole("tab").map((tab) => tab.textContent)).toEqual([
			"operations",
			"events",
		]);
	});
});
