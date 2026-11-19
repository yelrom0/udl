/**
 * ----------------
 * - result.test.ts -
 * ----------------
 *
 * `shapeOf`: what a response is, as the generated UI shows it — a table (a list of objects, or an
 * object whose one list is `items`, `accounts`, `events`…), a description list (a flat object),
 * or JSON. The columns are the item's scalar fields, `x-udl-ui.order` first.
 */

import type { JsonValue } from "@udl/ui-sdk";
import { describe, expect, it } from "vitest";
import { shapeOf } from "./result";

const accountInfo = {
	type: "object",
	required: ["account", "enabled"],
	properties: {
		account: { type: "string", title: "Account", "x-udl-ui": { order: 1 } },
		enabled: { type: "boolean", title: "Enabled" },
		created_at: { type: "string", format: "date-time", title: "Created At" },
		homepage: { type: "string", format: "uri", title: "Homepage" },
		identity: { type: "object", properties: { email: { type: "string" } } },
		access: { type: "array", items: { type: "string" } },
	},
};

const accountList = {
	type: "object",
	required: ["accounts"],
	properties: { accounts: { type: "array", items: accountInfo, title: "Accounts" } },
};

const accounts: JsonValue = [
	{ account: "gmail:work", enabled: true, created_at: "2026-11-19T00:00:00Z" },
	{ account: "gmail:home", enabled: false, created_at: "2026-11-18T00:00:00Z" },
];

describe("a list of objects", () => {
	it("is a table, its columns the scalar fields", () => {
		const shape = shapeOf({ type: "array", items: accountInfo }, accounts);

		expect(shape.kind).toBe("table");
		if (shape.kind !== "table") {
			throw new Error("not a table");
		}
		expect(shape.columns.map((column) => column.name)).toEqual([
			"account",
			"enabled",
			"created_at",
			"homepage",
		]);
		expect(shape.rows).toHaveLength(2);
	});

	it("is a table for an empty list too, with its columns", () => {
		const shape = shapeOf({ type: "array", items: accountInfo }, []);

		expect(shape.kind).toBe("table");
		if (shape.kind !== "table") {
			throw new Error("not a table");
		}
		expect(shape.rows).toEqual([]);
		expect(shape.columns).toHaveLength(4);
	});
});

describe("an object whose one list is its rows", () => {
	it("is a table of that list, named by it", () => {
		const shape = shapeOf(accountList, { accounts });

		expect(shape.kind).toBe("table");
		if (shape.kind !== "table") {
			throw new Error("not a table");
		}
		expect(shape.caption).toBe("Accounts");
		expect(shape.rows).toHaveLength(2);
		expect(shape.columns.map((column) => column.name)).toContain("account");
	});

	it("is a table for `events`, as list_events' response is", () => {
		const eventList = {
			type: "object",
			properties: {
				events: {
					type: "array",
					title: "Events",
					items: {
						type: "object",
						properties: {
							id: { type: "string", title: "Id" },
							depth: { type: "integer", title: "Depth" },
							data: { type: "object", additionalProperties: true },
						},
					},
				},
			},
		};

		const shape = shapeOf(eventList, { events: [{ id: "e1", depth: 0, data: { a: 1 } }] });

		expect(shape.kind).toBe("table");
		if (shape.kind !== "table") {
			throw new Error("not a table");
		}
		expect(shape.columns.map((column) => column.name)).toEqual(["id", "depth"]);
	});
});

describe("an object that is not a list", () => {
	it("is its fields, when they are all scalars", () => {
		const status = {
			type: "object",
			properties: {
				version: { type: "string", title: "Version" },
				healthy: { type: "boolean", title: "Healthy" },
			},
		};

		const shape = shapeOf(status, { version: "1.2.3", healthy: true });

		expect(shape.kind).toBe("fields");
		if (shape.kind !== "fields") {
			throw new Error("not fields");
		}
		expect(shape.fields.map((field) => field.name)).toEqual(["version", "healthy"]);
	});

	it("is JSON when a value is nested", () => {
		const shape = shapeOf(
			{ type: "object", properties: { data: { type: "object" } } },
			{ data: { a: 1 } },
		);

		expect(shape.kind).toBe("json");
	});

	it("is JSON when it has two lists (which one is the table?)", () => {
		const shape = shapeOf(
			{
				type: "object",
				properties: {
					a: { type: "array", items: { type: "object" } },
					b: { type: "array", items: { type: "object" } },
				},
			},
			{ a: [{ x: 1 }], b: [{ y: 2 }] },
		);

		expect(shape.kind).toBe("json");
	});
});

describe("a list the schema describes in no detail", () => {
	it("is still a table of what is there, with no columns to name", () => {
		const shape = shapeOf({ type: "object" }, { rows: [{ a: 1 }, { a: 2 }] });

		expect(shape.kind).toBe("table");
		if (shape.kind !== "table") {
			throw new Error("not a table");
		}
		expect(shape.rows).toHaveLength(2);
		expect(shape.columns).toEqual([]);
	});
});

describe("a list that is not all objects", () => {
	it("is JSON, including one with nothing in it", () => {
		expect(shapeOf({ type: "array" }, [null]).kind).toBe("json");
	});
	it("is JSON, and so is an empty list the schema says nothing about", () => {
		expect(shapeOf({ type: "array" }, ["a", "b"]).kind).toBe("json");
		expect(shapeOf({ type: "array" }, [1, { a: 1 }]).kind).toBe("json");
		expect(shapeOf({ type: "object", properties: {} }, { rows: [] }).kind).toBe("json");
	});
});

describe("a schema that says nothing", () => {
	it("is JSON: there is no field to name a value with", () => {
		expect(shapeOf({}, { a: 1, b: "two" }).kind).toBe("json");
	});

	it("is still a description list for the fields it does name", () => {
		const shape = shapeOf(
			{ type: "object", properties: { a: { type: "string" } } },
			{ a: "one", b: "two" },
		);

		expect(shape.kind).toBe("fields");
		if (shape.kind !== "fields") {
			throw new Error("not fields");
		}
		expect(shape.fields.map((field) => field.name)).toEqual(["a"]);
	});
});

describe("what is not an object", () => {
	it("is JSON", () => {
		expect(shapeOf({ type: "string" }, "hello").kind).toBe("json");
		expect(shapeOf({ type: "number" }, 3).kind).toBe("json");
		expect(shapeOf({ type: "null" }, null).kind).toBe("json");
	});
});

describe("a schema with references", () => {
	it("is read with them resolved", () => {
		const document = {
			components: { schemas: { AccountInfo: accountInfo } },
		};
		const schema = {
			type: "object",
			properties: {
				accounts: {
					type: "array",
					items: { $ref: "#/components/schemas/AccountInfo" },
					title: "Accounts",
				},
			},
		};

		const shape = shapeOf(schema, { accounts }, document);

		expect(shape.kind).toBe("table");
		if (shape.kind !== "table") {
			throw new Error("not a table");
		}
		expect(shape.columns.map((column) => column.name)).toContain("enabled");
	});
});

describe("the order of the columns", () => {
	it("is x-udl-ui.order first, then the schema's", () => {
		const schema = {
			type: "object",
			properties: {
				first: { type: "string" },
				second: { type: "string", "x-udl-ui": { order: -1 } },
				third: { type: "string" },
			},
		};

		const shape = shapeOf({ type: "array", items: schema }, [{ first: "a", second: "b" }]);

		if (shape.kind !== "table") {
			throw new Error("not a table");
		}
		expect(shape.columns.map((column) => column.name)).toEqual(["second", "first", "third"]);
	});
});

describe("a fan-out's answer (11.4)", () => {
	// (`FanOutResult[T]`: `{items: [{account, item}], failures, next_pages}`.)
	const fanOut = {
		type: "object",
		properties: {
			items: {
				type: "array",
				items: {
					type: "object",
					properties: {
						account: { type: "string", title: "Account" },
						item: {
							type: "object",
							properties: {
								id: { type: "string", title: "Id" },
								subject: { type: "string", title: "Subject" },
							},
						},
					},
				},
			},
			failures: {
				type: "array",
				items: {
					type: "object",
					properties: { account: { type: "string" }, error: { type: "string" } },
				},
			},
			next_pages: { type: "object" },
		},
	};

	it("is a table of the items, with the account they came from beside them", () => {
		const found = shapeOf(fanOut, {
			items: [
				{ account: "gmail:work", item: { id: "m1", subject: "Hi" } },
				{ account: "gmail:home", item: { id: "m2", subject: "Hello" } },
			],
			failures: [],
			next_pages: {},
		});

		expect(found.kind).toBe("table");
		if (found.kind !== "table") {
			throw new Error("not a table");
		}
		expect(found.columns.map((column) => column.name)).toEqual(["account", "id", "subject"]);
		expect(found.rows).toEqual([
			{ account: "gmail:work", id: "m1", subject: "Hi" },
			{ account: "gmail:home", id: "m2", subject: "Hello" },
		]);
	});

	it("keeps its columns when every account failed, and says why each did", () => {
		const found = shapeOf(fanOut, {
			items: [],
			failures: [
				{ account: "gmail:work", error: { code: "rate_limited", message: "out of credit" } },
			],
			next_pages: {},
		});

		expect(found.kind).toBe("table");
		if (found.kind !== "table") {
			throw new Error("not a table");
		}
		expect(found.columns.map((column) => column.name)).toEqual(["account", "id", "subject"]);
		expect(found.rows).toEqual([]);
		expect(found.failures).toEqual([
			{ account: "gmail:work", code: "rate_limited", message: "out of credit" },
		]);
	});

	it("is not one for a page of items that the schema does not call accounts'", () => {
		// (`{items: [], next_page: null}` is a page of one list, not a fan-out's answer: its one
		// column is the item's `id`, and an empty page has no phantom "Account" column.)
		const page = {
			type: "object",
			properties: {
				items: { type: "array", items: { type: "object", properties: { id: { type: "string" } } } },
				next_page: { type: "string" },
			},
		};

		const found = shapeOf(page, { items: [], next_page: null });

		expect(found.kind).toBe("table");
		if (found.kind !== "table") {
			throw new Error("not a table");
		}
		expect(found.columns.map((column) => column.name)).toEqual(["id"]);
		expect(found.rows).toEqual([]);
	});

	it("has one account column even when the item has an account of its own", () => {
		const own = {
			type: "object",
			properties: {
				items: {
					type: "array",
					items: {
						type: "object",
						properties: {
							account: { type: "string", title: "Account" },
							item: {
								type: "object",
								properties: {
									account: { type: "string", title: "Account" },
									id: { type: "string", title: "Id" },
								},
							},
						},
					},
				},
			},
		};

		const found = shapeOf(own, {
			items: [{ account: "gmail:work", item: { account: "x", id: "m1" } }],
		});

		expect(found.kind).toBe("table");
		if (found.kind !== "table") {
			throw new Error("not a table");
		}
		expect(found.columns.map((column) => column.name)).toEqual(["account", "id"]);
		expect(found.rows).toEqual([{ account: "gmail:work", id: "m1" }]);
	});
});
