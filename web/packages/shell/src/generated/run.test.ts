/**
 * --------------
 * - run.test.ts -
 * --------------
 *
 * Running a generated operation: a form's values split into its route's path and its body, and
 * what udl said about one it refused.
 */

import { FakeUdl } from "@udl/api/testing";
import { describe, expect, it } from "vitest";
import { pathNames, requestFor, runOperation, saidBy } from "./run";
import type { GeneratedOperation } from "./spec";

const OPERATION: GeneratedOperation = {
	name: "check_account",
	title: "Check",
	summary: "",
	description: "",
	hints: {
		read_only: false,
		destructive: false,
		idempotent: true,
		open_world: true,
		admin: false,
		mcp: true,
	},
	ui: { group: null, order: 0, view: "action", icon: null, hidden: false, confirm_field: null },
	method: "POST",
	path: "/accounts/{account}/check",
	request_schema: {},
	response_schema: {},
};

/** An operation, with the fields a test does not name as `OPERATION`'s. */
function operation(name: string, overrides: Partial<GeneratedOperation> = {}): GeneratedOperation {
	return { ...OPERATION, name, ...overrides };
}

describe("an operation's request", () => {
	it("takes the route's path parameters out of the form's values", () => {
		expect(pathNames("check_account")).toEqual(["account"]);
		// (An operation with no route of its own is udl's: the API's route is looked up by name.)
		expect(pathNames(operation("check_account", { method: null, path: null }))).toEqual([
			"account",
		]);
		expect(requestFor("check_account", { account: "gmail:work" })).toEqual({
			params: { account: "gmail:work" },
			body: undefined,
		});
	});

	it("calls a plugin's operation by the route udl named for it", async () => {
		const fake = new FakeUdl();
		let sent: unknown = null;
		fake.handle("list_sites", () => ({ sites: [] }));
		// (The plugin's own route, which the API's schema does not hold.)
		const api = fake.api();
		const at = (method: string, path: string, body?: unknown) => {
			sent = { method, path, body };
			return Promise.resolve({ things: [{ id: "t1", text: "one" }] });
		};

		const answered = await runOperation(
			{ ...api, callAt: at },
			operation("fake_list_things", {
				method: "POST",
				path: "/integrations/fake/list_things",
			}),
			{ account: "fake:work", contains: "" },
		);

		expect(sent).toEqual({
			method: "POST",
			path: "/integrations/fake/list_things",
			body: { account: "fake:work", contains: "" },
		});
		expect(answered).toEqual({ things: [{ id: "t1", text: "one" }] });
	});

	it("sends an empty body for a form with nothing in it", async () => {
		let sent: unknown = null;
		const at = (method: string, path: string, body?: unknown) => {
			sent = { method, path, body };
			return Promise.resolve({ items: [] });
		};
		const fake = new FakeUdl();

		await runOperation(
			{ ...fake.api(), callAt: at },
			operation("fake_list_things", {
				method: "POST",
				path: "/integrations/fake/list_things",
			}),
			{},
		);

		// (A body is what udl's route asks for, even when every field may be left out.)
		expect(sent).toEqual({
			method: "POST",
			path: "/integrations/fake/list_things",
			body: {},
		});
	});

	it("hands a read's fields to the client, which makes them its query", async () => {
		// (A GET cannot have a body, and udl takes a read's fields as query parameters: the
		// client builds that, from the same body a write would be sent.)
		let sent: unknown = null;
		const at = (method: string, path: string, body?: unknown) => {
			sent = { method, path, body };
			return Promise.resolve({});
		};
		const fake = new FakeUdl();

		await runOperation(
			{ ...fake.api(), callAt: at },
			operation("fake_find", {
				method: "GET",
				path: "/integrations/fake/find/{id}",
			}),
			{ id: "t1", text: "one two", tags: ["a", "b"] },
		);

		expect(sent).toEqual({
			method: "GET",
			path: "/integrations/fake/find/{id}",
			body: { text: "one two", tags: ["a", "b"] },
		});
	});

	it("runs udl's own read by name, with its fields", async () => {
		// (24.6's item: `list_downloads` takes `limit`, `offset`, `site_key` and `status` as its
		// query — a read the page may type into and run again.)
		const named: unknown[] = [];
		const api = {
			...new FakeUdl().api(),
			callNamed: (id: string, body?: unknown, params?: Record<string, string>) => {
				named.push({ id, body, params });
				return Promise.resolve({});
			},
		};

		await runOperation(api, operation("list_downloads", { method: null, path: null }), {
			limit: 5,
		});

		expect(named).toEqual([{ id: "list_downloads", body: { limit: 5 }, params: {} }]);
	});

	it("splits a route's path parameters out of the body", () => {
		const one = operation("fake_thing", { path: "/integrations/fake/thing/{thing}" });

		expect(requestFor(one, { thing: "t1", text: "one" })).toEqual({
			params: { thing: "t1" },
			body: { text: "one" },
		});
	});

	it("has no path at all for an operation the API does not have", () => {
		expect(pathNames("not_an_operation")).toEqual([]);
		expect(requestFor("not_an_operation", { anything: "goes" })).toEqual({
			params: {},
			body: { anything: "goes" },
		});
	});

	it("sends the rest as the body, and no body when there is no rest", () => {
		expect(requestFor("connect_account", { integration: "gmail", name: "work" })).toEqual({
			params: {},
			body: { integration: "gmail", name: "work" },
		});
		expect(requestFor("list_sites", {})).toEqual({ params: {}, body: undefined });
	});

	it("sends a number in a path as its text", () => {
		expect(requestFor("get_download", { id: 7 })).toEqual({ params: { id: "7" }, body: undefined });
	});

	it("is sent as the operation's own route", async () => {
		const fake = new FakeUdl().handle("check_account", () => ({
			account: "gmail:work",
			ok: true,
		}));

		await runOperation(fake.api(), OPERATION, { account: "gmail:work" });

		// (Its route is called with an empty body: a route's body is required even when it has
		// no fields — and udl's own routes ignore one they do not read.)
		expect(fake.calls[0]).toEqual({
			id: "check_account",
			body: {},
			params: { account: "gmail:work" },
		});
	});

	it("says udl's message and hint when it refuses the operation", () => {
		const refused = Object.assign(new Error("not allowed"), {
			info: { message: "not allowed", hint: "connect an account first" },
		});

		expect(saidBy(refused)).toEqual({ message: "not allowed", hint: "connect an account first" });
		expect(saidBy(new Error("boom"))).toEqual({ message: "boom", hint: null });
		expect(saidBy("boom")).toEqual({ message: "boom", hint: null });
	});
});
