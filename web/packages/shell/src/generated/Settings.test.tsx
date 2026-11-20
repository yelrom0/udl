/**
 * ----------------
 * - Settings.test.tsx -
 * ----------------
 *
 * A plugin's settings: the form udl's schema draws, the fields only the config file sets shown
 * and not editable, a secret as a password box, and Save — what it sends, and what it says when
 * udl refuses it.
 */

import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import type { components } from "@udl/api";
import { FakeUdl } from "@udl/api/testing";
import { describe, expect, it } from "vitest";
import { SettingsTab } from "./Settings";
import type { GeneratedPlugin } from "./spec";

function plugin(): GeneratedPlugin {
	return {
		name: "gmail",
		kind: "udl.integrations",
		title: "Gmail",
		summary: "",
		has_ui_plugin: false,
		operations: [],
		settings: {
			json_schema: {
				type: "object",
				properties: {
					label: { type: "string", title: "label" },
					client_id: { type: "string", title: "client id" },
					token: { type: "string", title: "token" },
				},
			},
			read_only: ["client_id"],
			secret: ["token"],
		},
		accounts: null,
		event_types: [],
	};
}

function info(): components["schemas"]["PluginSettingsInfo"] {
	return {
		name: "gmail",
		kind: "udl.integrations",
		json_schema: {
			type: "object",
			properties: {
				label: { type: "string", title: "label" },
				client_id: { type: "string", title: "client id" },
				token: { type: "string", title: "token" },
			},
		},
		values: {},
		sources: {},
		file_only: ["client_id"],
		secrets: ["token"],
	};
}

describe("a plugin's settings", () => {
	it("draws the form udl's schema says, its file-only field set elsewhere", async () => {
		const fake = new FakeUdl().handle("get_plugin_settings", () => info());
		render(<SettingsTab plugin={plugin()} api={fake.api()} />);

		const clientId = await screen.findByLabelText(/client id/);
		expect(clientId.hasAttribute("disabled")).toBe(true);
		expect(screen.getByText("set in the config file")).toBeDefined();
		expect(screen.getByLabelText("token").getAttribute("type")).toBe("password");
	});

	it("draws the settings udl says it runs with, not only the schema's defaults", async () => {
		const fake = new FakeUdl().handle("get_plugin_settings", () => ({
			...info(),
			values: { label: "the inbox", token: "" },
		}));
		render(<SettingsTab plugin={plugin()} api={fake.api()} />);

		// (What is saved is what the form shows — and a secret is never drawn back.)
		expect(await screen.findByLabelText("label")).toHaveProperty("value", "the inbox");
		expect(screen.getByLabelText("token")).toHaveProperty("value", "");
	});

	it("sends nothing it did not change, and never a file-only field", async () => {
		// (udl's answer is the whole layered model: a file-only field, one the config file sets
		// and the defaults are all in it. udl refuses a file-only field in a save, and sending
		// an unchanged value back would shadow the config file from then on.)
		const fake = new FakeUdl().handle("get_plugin_settings", () => ({
			...info(),
			values: { client_id: "the-one-the-config-file-sets", label: "the inbox" },
		}));
		let sent: unknown = null;
		fake.handle("set_plugin_settings", ({ body }) => {
			sent = body;
			return info();
		});
		render(<SettingsTab plugin={plugin()} api={fake.api()} />);

		fireEvent.change(await screen.findByLabelText("label"), { target: { value: "inbox" } });
		fireEvent.click(screen.getByRole("button", { name: "Save" }));

		await waitFor(() => expect(sent).not.toBeNull());
		expect(sent).toEqual({ values: { label: "inbox" } });
	});

	it("sends no value at all when nothing changed", async () => {
		const fake = new FakeUdl().handle("get_plugin_settings", () => ({
			...info(),
			values: { label: "the inbox" },
		}));
		let sent: unknown = null;
		fake.handle("set_plugin_settings", ({ body }) => {
			sent = body;
			return info();
		});
		render(<SettingsTab plugin={plugin()} api={fake.api()} />);

		await screen.findByLabelText("label");
		fireEvent.click(screen.getByRole("button", { name: "Save" }));

		await waitFor(() => expect(sent).not.toBeNull());
		expect(sent).toEqual({ values: {} });
	});

	it("does not send a secret it did not touch, only the mask udl gave it", async () => {
		// (udl says a set secret as a mask; sending that back would store the mask as the secret.)
		const fake = new FakeUdl().handle("get_plugin_settings", () => ({
			...info(),
			values: { label: "the inbox", token: "**********" },
		}));
		let sent: unknown = null;
		fake.handle("set_plugin_settings", ({ body }) => {
			sent = body;
			return info();
		});
		render(<SettingsTab plugin={plugin()} api={fake.api()} />);

		// (The box says it is set, and does not draw it back.)
		expect(await screen.findByLabelText("token")).toHaveProperty(
			"placeholder",
			"(set: type to replace)",
		);
		fireEvent.click(screen.getByRole("button", { name: "Save" }));

		await waitFor(() => expect(sent).not.toBeNull());
		expect(sent).toEqual({ values: {} });
	});

	it("sends a secret that was typed in", async () => {
		const fake = new FakeUdl().handle("get_plugin_settings", () => ({
			...info(),
			values: { label: "the inbox", token: "**********" },
		}));
		let sent: unknown = null;
		fake.handle("set_plugin_settings", ({ body }) => {
			sent = body;
			return info();
		});
		render(<SettingsTab plugin={plugin()} api={fake.api()} />);

		fireEvent.change(await screen.findByLabelText("token"), { target: { value: "new-one" } });
		fireEvent.click(screen.getByRole("button", { name: "Save" }));

		await waitFor(() => expect(sent).not.toBeNull());
		// (Only the secret: the label beside it is unchanged, and is not sent again.)
		expect(sent).toEqual({ values: { token: "new-one" } });
	});

	it("sends what was changed, and says it saved", async () => {
		const fake = new FakeUdl().handle("get_plugin_settings", () => info());
		let sent: unknown = null;
		fake.handle("set_plugin_settings", ({ body }) => {
			sent = body;
			return info();
		});
		render(<SettingsTab plugin={plugin()} api={fake.api()} />);

		fireEvent.change(await screen.findByLabelText("label"), { target: { value: "inbox" } });
		fireEvent.click(screen.getByRole("button", { name: "Save" }));

		expect(await screen.findByRole("status")).toHaveProperty("textContent", "Saved");
		expect(sent).toEqual({ values: { label: "inbox" } });
	});

	it("says what udl said when it refused them", async () => {
		const fake = new FakeUdl().handle("get_plugin_settings", () => info());
		fake.handle("set_plugin_settings", () => {
			throw Object.assign(new Error("no"), {
				info: { message: "it refused the settings", hint: "the key is too short" },
			});
		});
		render(<SettingsTab plugin={plugin()} api={fake.api()} />);

		await screen.findByLabelText("label");
		fireEvent.click(screen.getByRole("button", { name: "Save" }));

		const alert = await screen.findByRole("alert");
		expect(alert.textContent).toContain("it refused the settings");
		expect(alert.textContent).toContain("the key is too short");
	});

	it("says what udl said when it could not read them", async () => {
		const fake = new FakeUdl().handle("get_plugin_settings", () => {
			throw new Error("the config file is broken");
		});
		render(<SettingsTab plugin={plugin()} api={fake.api()} />);

		expect(await screen.findByRole("alert")).toHaveProperty(
			"textContent",
			expect.stringContaining("the config file is broken"),
		);
	});

	it("cannot be saved before the settings are read", () => {
		const fake = new FakeUdl();
		render(<SettingsTab plugin={plugin()} api={fake.api()} />);

		expect(screen.getByRole("button", { name: "Save" }).hasAttribute("disabled")).toBe(true);
		expect(screen.getByLabelText("Loading the settings")).toBeDefined();
	});

	it("draws nothing when the page closes while the settings load", async () => {
		const fake = new FakeUdl();
		const waiting: { finish?: (value: components["schemas"]["PluginSettingsInfo"]) => void } = {};
		fake.handle(
			"get_plugin_settings",
			() =>
				new Promise<components["schemas"]["PluginSettingsInfo"]>((settle) => {
					waiting.finish = settle;
				}),
		);
		const view = render(<SettingsTab plugin={plugin()} api={fake.api()} />);
		view.unmount();
		waiting.finish?.(info());

		await new Promise((settle) => setTimeout(settle, 0));
		expect(fake.calls.filter((call) => call.id === "get_plugin_settings")).toHaveLength(1);
	});

	it("draws nothing when the page closes while it saves", async () => {
		const fake = new FakeUdl().handle("get_plugin_settings", () => info());
		const waiting: { finish?: (value: components["schemas"]["PluginSettingsInfo"]) => void } = {};
		fake.handle(
			"set_plugin_settings",
			() =>
				new Promise<components["schemas"]["PluginSettingsInfo"]>((settle) => {
					waiting.finish = settle;
				}),
		);
		const view = render(<SettingsTab plugin={plugin()} api={fake.api()} />);
		fireEvent.change(await screen.findByLabelText("label"), { target: { value: "inbox" } });
		fireEvent.click(screen.getByRole("button", { name: "Save" }));
		view.unmount();
		waiting.finish?.(info());

		await new Promise((settle) => setTimeout(settle, 0));
		expect(fake.calls.filter((call) => call.id === "set_plugin_settings")).toHaveLength(1);
	});

	it("does not draw a saved secret back", async () => {
		const fake = new FakeUdl().handle("get_plugin_settings", () => info());
		fake.handle("set_plugin_settings", () => info());
		render(<SettingsTab plugin={plugin()} api={fake.api()} />);

		const token = await screen.findByLabelText("token");
		fireEvent.change(token, { target: { value: "s3cret" } });
		fireEvent.click(screen.getByRole("button", { name: "Save" }));

		await screen.findByRole("status");
		await waitFor(() =>
			expect((screen.getByLabelText("token") as HTMLInputElement).value).toBe(""),
		);
	});
});
