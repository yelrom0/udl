/**
 * -------------------
 * - Accounts.test.tsx -
 * -------------------
 *
 * An integration's accounts: what udl lists, checking one (and what a failed check says),
 * disconnecting one only when asked, and connecting one — with tokens, with options, by OAuth,
 * and the ways an integration offers when it offers more than one.
 */

import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import type { components } from "@udl/api";
import { FakeUdl } from "@udl/api/testing";
import { beforeEach, describe, expect, it } from "vitest";
import { ConfirmHost } from "../components/ConfirmHost";
import { confirms } from "../stores/confirms";
import { AccountsTab } from "./Accounts";
import type { GeneratedPlugin } from "./spec";

/** An account, as the API's schema holds one. */
type Account = components["schemas"]["AccountInfo"];

/** What connecting one answers. */
type ConnectAnswer = components["schemas"]["Connection"];

function plugin(overrides: Partial<GeneratedPlugin> = {}): GeneratedPlugin {
	return {
		name: "gmail",
		kind: "udl.integrations",
		title: "Gmail",
		summary: "",
		has_ui_plugin: false,
		operations: [],
		settings: null,
		accounts: { connect: ["tokens"], access_levels: ["read"], options_schema: {} },
		event_types: [],
		...overrides,
	};
}

/** An account as udl lists one (its `id` is udl's own, its `account` the ref the API takes). */
function account(integration: string, name: string, overrides: Partial<Account> = {}): Account {
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
		...overrides,
	};
}

function draw(one = plugin(), fake = new FakeUdl()) {
	render(
		<>
			<AccountsTab plugin={one} api={fake.api()} />
			<ConfirmHost />
		</>,
	);
	return fake;
}

beforeEach(() => {
	// (A question left open by a test is not the next test's.)
	confirms.setState({ asked: [] });
});

describe("an integration's accounts", () => {
	it("lists only its own accounts", async () => {
		const fake = draw();
		fake.handle("list_accounts", () => ({
			accounts: [account("gmail", "work"), account("slack", "team")],
		}));

		expect(await screen.findByText("gmail:work")).toBeDefined();
		expect(screen.queryByText("slack:team")).toBeNull();
	});

	it("says it has none yet", async () => {
		draw().handle("list_accounts", () => ({ accounts: [] }));

		expect(await screen.findByText("No accounts yet.")).toBeDefined();
	});

	it("says what udl said when it could not list them", async () => {
		draw().handle("list_accounts", () => {
			throw new Error("the store is locked");
		});

		expect(await screen.findByRole("alert")).toHaveProperty(
			"textContent",
			expect.stringContaining("the store is locked"),
		);
	});

	it("says an account that was never connected", async () => {
		const fake = draw();
		fake.handle("list_accounts", () => ({
			accounts: [{ ...account("gmail", "work"), connected_at: null }],
		}));

		expect(await screen.findByText("Not connected")).toBeDefined();
	});

	it("checks one, and says what a failed check found", async () => {
		const fake = draw();
		fake.handle("list_accounts", () => ({ accounts: [account("gmail", "work")] }));
		let asked: unknown = null;
		fake.handle("check_account", ({ params }) => {
			asked = params;
			return { account: "gmail:work", ok: false, problems: ["the token expired"] };
		});

		await screen.findByText("gmail:work");
		fireEvent.click(screen.getByRole("button", { name: "Check" }));

		expect(await screen.findByText("the token expired")).toBeDefined();
		// (udl takes the account's ref: its own id is not a name an account has.)
		expect(asked).toEqual({ account: "gmail:work" });
	});

	it("disconnects one only when asked, and lists again", async () => {
		const fake = draw();
		let accounts = [account("gmail", "work")];
		fake.handle("list_accounts", () => ({ accounts }));
		let asked: unknown = null;
		fake.handle("disconnect_account", ({ params }) => {
			asked = params;
			accounts = [];
			return { deleted: true, revoked: null, subscriptions: [] };
		});

		await screen.findByText("gmail:work");
		fireEvent.click(screen.getByRole("button", { name: "Disconnect" }));
		const dialog = await screen.findByRole("dialog");
		fireEvent.click(within(dialog).getByRole("button", { name: "Cancel" }));

		expect(fake.calls.filter((call) => call.id === "disconnect_account")).toHaveLength(0);
		expect(screen.getByText("gmail:work")).toBeDefined();

		fireEvent.click(screen.getByRole("button", { name: "Disconnect" }));
		const again = await screen.findByRole("dialog");
		fireEvent.click(within(again).getByRole("button", { name: "Disconnect" }));

		expect(await screen.findByText("No accounts yet.")).toBeDefined();
		expect(asked).toEqual({ account: "gmail:work" });
	});

	it("connects one with its tokens, and the access it was given", async () => {
		const fake = draw(
			plugin({
				accounts: { connect: ["tokens"], access_levels: ["read", "send"], options_schema: {} },
			}),
		);
		fake.handle("list_accounts", () => ({ accounts: [] }));
		fake.handle("connect_account", () => ({
			account: account("gmail", "work"),
			authorization_url: null,
		}));

		await screen.findByText("No accounts yet.");
		fireEvent.change(screen.getByLabelText("Name"), { target: { value: "work" } });
		fireEvent.click(screen.getByLabelText("send"));
		fireEvent.change(screen.getByLabelText("Token name"), { target: { value: "refresh_token" } });
		fireEvent.click(screen.getByRole("button", { name: "Add token" }));
		fireEvent.change(screen.getByLabelText("refresh_token"), { target: { value: "s3cret" } });
		fireEvent.click(screen.getByRole("button", { name: "Connect" }));

		await waitFor(() =>
			expect(fake.calls.find((call) => call.id === "connect_account")?.body).toEqual({
				integration: "gmail",
				name: "work",
				reconnect: false,
				access: ["read", "send"],
				tokens: { refresh_token: "s3cret" },
			}),
		);
	});

	it("draws the tokens udl says an account takes, with how each is shown (29.9)", async () => {
		const fake = draw(
			plugin({
				accounts: {
					connect: ["tokens"],
					access_levels: ["read"],
					options_schema: {},
					tokens_schema: {
						type: "object",
						required: ["token"],
						properties: {
							token: {
								type: "string",
								writeOnly: true,
								"x-udl-ui": {
									label: "Token",
									placeholder: "ghp_… or op://vault/item/field",
									help: "Or a 1Password reference, read when used and never stored.",
								},
							},
						},
					},
				},
			}),
		);
		fake.handle("list_accounts", () => ({ accounts: [] }));
		fake.handle("connect_account", () => ({
			account: account("gmail", "work"),
			authorization_url: null,
		}));

		await screen.findByText("No accounts yet.");
		fireEvent.change(screen.getByLabelText("Name"), { target: { value: "work" } });
		// (udl's hints, not a name typed by hand: the box says what goes in it.)
		expect(screen.getByPlaceholderText("ghp_… or op://vault/item/field")).toBeDefined();
		expect(
			screen.getByText("Or a 1Password reference, read when used and never stored."),
		).toBeDefined();
		fireEvent.change(screen.getByLabelText(/Token/), { target: { value: "ghp_x" } });
		fireEvent.click(screen.getByRole("button", { name: "Connect" }));

		await waitFor(() =>
			expect(fake.calls.find((call) => call.id === "connect_account")?.body).toEqual({
				integration: "gmail",
				name: "work",
				reconnect: false,
				access: ["read"],
				tokens: { token: "ghp_x" },
			}),
		);
	});

	it("takes an access level off again", async () => {
		const fake = draw(
			plugin({
				accounts: { connect: ["tokens"], access_levels: ["read", "send"], options_schema: {} },
			}),
		);
		fake.handle("list_accounts", () => ({ accounts: [] }));
		fake.handle("connect_account", () => ({
			account: account("gmail", "work"),
			authorization_url: null,
		}));

		await screen.findByText("No accounts yet.");
		fireEvent.change(screen.getByLabelText("Name"), { target: { value: "work" } });
		fireEvent.click(screen.getByLabelText("read"));
		fireEvent.click(screen.getByRole("button", { name: "Connect" }));

		await waitFor(() =>
			expect(fake.calls.find((call) => call.id === "connect_account")?.body).toEqual({
				integration: "gmail",
				name: "work",
				reconnect: false,
			}),
		);
	});

	it("leaves a token name it already has alone", async () => {
		const fake = draw();
		fake.handle("list_accounts", () => ({ accounts: [] }));

		await screen.findByText("No accounts yet.");
		fireEvent.change(screen.getByLabelText("Token name"), { target: { value: "token" } });
		fireEvent.click(screen.getByRole("button", { name: "Add token" }));
		fireEvent.change(screen.getByLabelText("Token name"), { target: { value: "token" } });
		fireEvent.click(screen.getByRole("button", { name: "Add token" }));

		// (One box, not two, and it is the one that was there.)
		expect(screen.getAllByLabelText("token")).toHaveLength(1);
	});

	it("draws nothing when the page closes while it connects", async () => {
		const fake = new FakeUdl();
		fake.handle("list_accounts", () => ({ accounts: [] }));
		const waiting: { finish?: (value: ConnectAnswer) => void } = {};
		fake.handle(
			"connect_account",
			() =>
				new Promise<ConnectAnswer>((settle) => {
					waiting.finish = settle;
				}),
		);
		const view = render(
			<>
				<AccountsTab plugin={plugin()} api={fake.api()} />
				<ConfirmHost />
			</>,
		);
		fireEvent.change(await screen.findByLabelText("Name"), { target: { value: "work" } });
		fireEvent.click(screen.getByRole("button", { name: "Connect" }));
		view.unmount();
		waiting.finish?.({ account: account("gmail", "work"), authorization_url: null });

		await new Promise((settle) => setTimeout(settle, 0));
		expect(fake.calls.filter((call) => call.id === "connect_account")).toHaveLength(1);
	});

	it("forgets a token that was added by mistake", async () => {
		const fake = draw();
		fake.handle("list_accounts", () => ({ accounts: [] }));

		await screen.findByText("No accounts yet.");
		fireEvent.change(screen.getByLabelText("Token name"), { target: { value: "wrong" } });
		fireEvent.click(screen.getByRole("button", { name: "Add token" }));
		expect(screen.getByLabelText("wrong")).toBeDefined();

		fireEvent.click(screen.getByRole("button", { name: "Forget wrong" }));

		expect(screen.queryByLabelText("wrong")).toBeNull();
	});

	it("keeps the access it starts with for the next account", async () => {
		// (What for starts at the integration's first level: connecting one account must not
		// leave the form sending none at all — with one level, the fieldset is not even shown.)
		const fake = draw(
			plugin({
				accounts: { connect: ["tokens"], access_levels: ["read", "send"], options_schema: {} },
			}),
		);
		fake.handle("list_accounts", () => ({ accounts: [] }));
		fake.handle("connect_account", () => ({
			account: account("gmail", "work"),
			authorization_url: null,
		}));

		await screen.findByText("No accounts yet.");
		fireEvent.change(screen.getByLabelText("Name"), { target: { value: "one" } });
		fireEvent.click(screen.getByRole("button", { name: "Connect" }));
		await waitFor(() =>
			expect(fake.calls.filter((call) => call.id === "connect_account")).toHaveLength(1),
		);

		fireEvent.change(screen.getByLabelText("Name"), { target: { value: "two" } });
		fireEvent.click(screen.getByRole("button", { name: "Connect" }));

		await waitFor(() =>
			expect(fake.calls.filter((call) => call.id === "connect_account")[1]?.body).toMatchObject({
				name: "two",
				access: ["read"],
			}),
		);
	});

	it("takes an answer without an authorization url as connected", async () => {
		// (`authorization_url` is optional in the schema: left out is no OAuth, not an OAuth
		// without a page.)
		const fake = draw(
			plugin({ accounts: { connect: ["tokens"], access_levels: ["read"], options_schema: {} } }),
		);
		fake.handle("list_accounts", () => ({ accounts: [] }));
		fake.handle("connect_account", () => ({ account: account("gmail", "work") }));

		await screen.findByText("No accounts yet.");
		fireEvent.change(screen.getByLabelText("Name"), { target: { value: "work" } });
		fireEvent.click(screen.getByRole("button", { name: "Connect" }));

		// (The accounts are read again, and the form is ready for the next one.)
		await waitFor(() =>
			expect(fake.calls.filter((call) => call.id === "list_accounts")).toHaveLength(2),
		);
		expect(screen.queryByText(/Finish connecting it in your browser/)).toBeNull();
		expect((screen.getByLabelText("Name") as HTMLInputElement).value).toBe("");
	});

	it("says Connect for a form, and Sign in only for OAuth", async () => {
		draw(plugin({ accounts: { connect: ["none"], access_levels: [], options_schema: {} } }));

		expect(await screen.findByRole("button", { name: "Connect" })).toBeTruthy();
		expect(screen.queryByRole("button", { name: "Sign in" })).toBeNull();
	});

	it("says where an OAuth connection finishes", async () => {
		const fake = draw(
			plugin({ accounts: { connect: ["oauth"], access_levels: ["read"], options_schema: {} } }),
		);
		fake.handle("list_accounts", () => ({ accounts: [] }));
		fake.handle("connect_account", () => ({
			account: account("gmail", "work"),
			authorization_url: "https://accounts.example/consent",
		}));

		await screen.findByText("No accounts yet.");
		fireEvent.change(screen.getByLabelText("Name"), { target: { value: "work" } });
		// (OAuth connects in the browser: the button is a sign-in, not a connect.)
		fireEvent.click(screen.getByRole("button", { name: "Sign in" }));

		const link = await screen.findByRole("link", { name: "https://accounts.example/consent" });
		expect(link.getAttribute("href")).toBe("https://accounts.example/consent");
	});

	it("offers the ways to connect, when there is more than one", async () => {
		const fake = draw(
			plugin({
				accounts: { connect: ["oauth", "tokens"], access_levels: ["read"], options_schema: {} },
			}),
		);
		fake.handle("list_accounts", () => ({ accounts: [] }));
		fake.handle("connect_account", () => ({
			account: account("gmail", "work"),
			authorization_url: null,
		}));

		await screen.findByText("No accounts yet.");
		fireEvent.change(screen.getByLabelText("Name"), { target: { value: "work" } });
		fireEvent.change(screen.getByLabelText("How"), { target: { value: "tokens" } });
		fireEvent.click(screen.getByRole("button", { name: "Connect" }));

		await waitFor(() =>
			expect(fake.calls.find((call) => call.id === "connect_account")?.body).toEqual({
				integration: "gmail",
				name: "work",
				reconnect: false,
				access: ["read"],
				method: "tokens",
			}),
		);
	});

	it("sends an integration's options when it has fields", async () => {
		const fake = draw(
			plugin({
				accounts: {
					connect: ["tokens"],
					access_levels: ["read"],
					options_schema: {
						type: "object",
						properties: { label: { type: "string", title: "label" } },
					},
				},
			}),
		);
		fake.handle("list_accounts", () => ({ accounts: [] }));
		fake.handle("connect_account", () => ({
			account: account("gmail", "work"),
			authorization_url: null,
		}));

		await screen.findByText("No accounts yet.");
		fireEvent.change(screen.getByLabelText("Name"), { target: { value: "work" } });
		fireEvent.change(screen.getByLabelText("label"), { target: { value: "inbox" } });
		fireEvent.click(screen.getByRole("button", { name: "Connect" }));

		await waitFor(() =>
			expect(fake.calls.find((call) => call.id === "connect_account")?.body).toEqual({
				integration: "gmail",
				name: "work",
				reconnect: false,
				access: ["read"],
				options: { label: "inbox" },
			}),
		);
	});

	it("says what udl said when it refused the connection", async () => {
		const fake = draw();
		fake.handle("list_accounts", () => ({ accounts: [] }));
		fake.handle("connect_account", () => {
			throw Object.assign(new Error("no"), {
				info: { message: "it refused the token", hint: "check the app's scopes" },
			});
		});

		await screen.findByText("No accounts yet.");
		fireEvent.change(screen.getByLabelText("Name"), { target: { value: "work" } });
		fireEvent.click(screen.getByRole("button", { name: "Connect" }));

		const alert = await screen.findByRole("alert");
		expect(alert.textContent).toContain("it refused the token");
		expect(alert.textContent).toContain("check the app's scopes");
	});

	it("says what udl said when a check could not be made", async () => {
		const fake = draw();
		fake.handle("list_accounts", () => ({ accounts: [account("gmail", "work")] }));
		fake.handle("check_account", () => {
			throw new Error("the network is down");
		});

		await screen.findByText("gmail:work");
		fireEvent.click(screen.getByRole("button", { name: "Check" }));

		expect(await screen.findByRole("alert")).toHaveProperty(
			"textContent",
			expect.stringContaining("the network is down"),
		);
	});

	it("says nothing of accounts when the plugin has none", () => {
		const fake = new FakeUdl();
		const { container } = render(
			<AccountsTab plugin={plugin({ accounts: null })} api={fake.api()} />,
		);

		expect(container.textContent).toBe("");
	});
});
