/**
 * What a plugin gets from the shell (`UIContext`): the API, the live events, the theme, the
 * stores, navigation, toasts and questions. It holds no state: everything is the shell's, read
 * through here.
 */

import { beforeEach, describe, expect, it, vi } from "vitest";
import { confirms } from "../stores/confirms";
import type { UIPluginInfo } from "../stores/plugins";
import { setTheme } from "../stores/theme";
import { toasts } from "../stores/toasts";
import { api } from "../api";
import { contextOf, TOKENS } from "./context";
import { forgetListeners, publishEvent } from "./events";

const GMAIL: UIPluginInfo = {
	name: "gmail",
	version: "0.1.0",
	base_url: "/ui/plugins/gmail/",
	entry_url: "/ui/plugins/gmail/index.js",
	styles: [],
	isolation: "shadow",
	contributes: {},
	plugin: "gmail",
};

const context = (navigate = vi.fn()) => contextOf({ info: GMAIL, navigate });

beforeEach(() => {
	forgetListeners();
	toasts.setState({ items: [] });
	confirms.setState({ asked: [] });
});

describe("a plugin's context", () => {
	it("says which plugin it is, and where its files are", () => {
		expect(context().plugin).toEqual({
			name: "gmail",
			version: "0.1.0",
			baseUrl: "/ui/plugins/gmail/",
		});
	});

	it("calls an operation by name", async () => {
		const fetch = vi.fn(async () => new Response(JSON.stringify({ sites: [] }), { status: 200 }));
		vi.stubGlobal("fetch", fetch);

		const answer = await context().call("list_sites");

		expect(fetch).toHaveBeenCalledWith("/sites", expect.anything());
		expect(answer).toEqual({ sites: [] });
		vi.unstubAllGlobals();
	});

	it("hands the events the page's connection brings to a listener that asked", () => {
		const heard = vi.fn();
		context().events.subscribe("gmail.*", heard);

		publishEvent({ type: "gmail.message.received", data: { subject: "Hi" } });

		expect(heard).toHaveBeenCalledWith({ type: "gmail.message.received", data: { subject: "Hi" } });
	});

	it("says the theme, and hears when it changes", () => {
		setTheme("dark");

		const seen: string[] = [];
		const ctx = context();
		const stop = ctx.theme.onChange((theme) => seen.push(theme.mode));

		expect(ctx.theme.mode).toBe("dark");
		expect(ctx.theme.name).toBe("dark");
		setTheme("light");
		expect(seen).toEqual(["light"]);
		stop();
		setTheme("dark");
		expect(seen).toEqual(["light"]);
	});

	it("reads the theme's tokens from the page", () => {
		const style = vi.spyOn(window, "getComputedStyle").mockReturnValue({
			getPropertyValue: (name: string) =>
				name === "--color-primary" ? " oklch(0.5 0.1 200) " : "",
		} as CSSStyleDeclaration);

		const tokens = context().theme.tokens;

		expect(tokens["--color-primary"]).toBe("oklch(0.5 0.1 200)");
		expect(Object.keys(tokens)).toEqual([...TOKENS]);
		style.mockRestore();
	});

	it("hands the shell's stores over, read-only", () => {
		const stores = context().stores;

		expect(stores.live?.getState()).toHaveProperty("connection");
		const heard = vi.fn();
		const stop = stores.toasts?.subscribe(heard);
		stop?.();
		expect(typeof stores.plugins?.getState()).toBe("object");
	});

	it("navigates where the plugin asks", () => {
		const navigate = vi.fn();

		context(navigate).navigate("/sites");

		expect(navigate).toHaveBeenCalledWith("/sites");
	});

	it("says something on the page, an info toast unless the plugin says otherwise", () => {
		context().toast("Saved");
		context().toast("It failed", "error");

		expect(toasts.getState().items.map((item) => [item.kind, item.message])).toEqual([
			["info", "Saved"],
			["error", "It failed"],
		]);
	});

	it("asks a question, and answers with what the user chose", async () => {
		const asked = context().confirm("Delete it?", { ok: "Delete" });

		expect(confirms.getState().asked[0]?.ok).toBe("Delete");
		confirms.getState().asked[0]?.settle(true);

		await expect(asked).resolves.toBe(true);
	});

	it("hands the typed client over too, for what a plugin knows how to ask for", () => {
		expect(context().api).toBe(api);
	});
});
