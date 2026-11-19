/**
 * ---------------
 * - schema.test.ts -
 * ---------------
 *
 * `fieldsOf`: a JSON Schema, as an ordered list of fields — what each is called, the widget that
 * shows it, whether it is required, its default, its choices, an object's own fields, and the
 * `x-udl-ui` hints. An unknown shape is the json widget, never a throw.
 */

import fc from "fast-check";
import { describe, expect, it } from "vitest";
import { type FieldSpec, fieldsOf, resolveRefs, valueAt, withoutValue, withValue } from "./schema";

function of(schema: unknown): readonly FieldSpec[] {
	return fieldsOf(schema as never);
}

const person = {
	type: "object",
	title: "Person",
	required: ["name", "address"],
	properties: {
		name: { type: "string", title: "Name", description: "Who they are." },
		age: { type: "integer", default: 30 },
		address: {
			type: "object",
			properties: {
				street: { type: "string" },
				city: { type: "string", "x-udl-ui": { label: "Town" } },
			},
		},
		newsletter: { type: "boolean", default: false },
		role: { type: "string", enum: ["admin", "user"] },
		secret: { type: "string", writeOnly: true },
		anything: {},
	},
};

describe("a field's widget", () => {
	it("is its type's: text, number, switch", () => {
		const found = of(person);

		expect(
			["name", "age", "newsletter"].map(
				(name) => found.find((field) => field.name === name)?.widget,
			),
		).toEqual(["text", "number", "switch"]);
	});

	it("is a select when it has choices, with each choice's label", () => {
		const found = of(person).find((field) => field.name === "role");

		expect(found?.widget).toBe("select");
		expect(found?.options).toEqual([
			{ value: "admin", label: "admin" },
			{ value: "user", label: "user" },
		]);
	});

	it("is a password for what is write-only", () => {
		expect(of(person).find((field) => field.name === "secret")?.widget).toBe("password");
	});

	it("follows a format: date-time, uri, duration", () => {
		const found = of({
			type: "object",
			properties: {
				at: { type: "string", format: "date-time" },
				url: { type: "string", format: "uri" },
				wait: { type: "string", format: "duration" },
			},
		});

		expect(found.map((field) => field.widget)).toEqual(["datetime", "url", "duration"]);
	});

	it("is what x-udl-ui says, over the type's", () => {
		const [found] = of({
			type: "object",
			properties: { text: { type: "string", "x-udl-ui": { widget: "textarea" } } },
		});

		expect(found?.widget).toBe("textarea");
	});

	it("is the type's when the hint names no widget udl has", () => {
		const [found] = of({
			type: "object",
			properties: { text: { type: "string", "x-udl-ui": { widget: "no-such-thing" } } },
		});

		expect(found?.widget).toBe("text");
	});

	it("is json for what it cannot tell (an object with no fields, an array, no type)", () => {
		const found = of({
			type: "object",
			properties: {
				box: { type: "object" },
				list: { type: "array", items: { type: "string" } },
				unknown: {},
			},
		});

		expect(found.map((field) => field.widget)).toEqual(["json", "json", "json"]);
	});
});

describe("a field that may be null", () => {
	it("is its type's, not a box of JSON", () => {
		const found = of({
			type: "object",
			properties: {
				note: { anyOf: [{ type: "string" }, { type: "null" }] },
				agree: { anyOf: [{ type: "null" }, { type: "boolean" }] },
				count: { oneOf: [{ type: "integer" }, { type: "null" }] },
			},
		});

		expect(found.map((field) => field.widget)).toEqual(["text", "switch", "number"]);
	});

	it("is a box of JSON when it really is a union", () => {
		const found = of({
			type: "object",
			properties: { either: { anyOf: [{ type: "string" }, { type: "integer" }] } },
		});

		expect(found[0]?.widget).toBe("json");
	});

	it("keeps what it says about itself: a write-only nullable password", () => {
		const found = of({
			type: "object",
			properties: {
				token: {
					anyOf: [{ type: "string", format: "password", writeOnly: true }, { type: "null" }],
				},
			},
		});

		expect([found[0]?.widget, found[0]?.writeOnly]).toEqual(["password", true]);
	});
});

describe("a field's name and help", () => {
	it("is its title, its description, and its name when it has neither", () => {
		const found = of(person);
		const name = found.find((field) => field.name === "name");
		const age = found.find((field) => field.name === "age");

		expect(name?.label).toBe("Name");
		expect(name?.help).toBe("Who they are.");
		expect(age?.label).toBe("age");
		expect(age?.help).toBeUndefined();
	});

	it("is what x-udl-ui says, over the schema's", () => {
		const [found] = of({
			type: "object",
			properties: {
				city: {
					type: "string",
					title: "City",
					description: "Where.",
					"x-udl-ui": { label: "Town", help: "Which town.", placeholder: "Berlin" },
				},
			},
		});

		expect([found?.label, found?.help, found?.placeholder]).toEqual([
			"Town",
			"Which town.",
			"Berlin",
		]);
	});
});

describe("a field's requiredness and default", () => {
	it("is the schema's", () => {
		const found = of(person);

		expect(found.find((field) => field.name === "name")?.required).toBe(true);
		expect(found.find((field) => field.name === "age")?.required).toBe(false);
	});

	it("is its default, when it has one", () => {
		const found = of(person);

		expect(found.find((field) => field.name === "age")?.default).toBe(30);
		expect(found.find((field) => field.name === "name")?.default).toBeUndefined();
	});
});

describe("an object's own fields", () => {
	it("are its properties, each with its path", () => {
		const address = of(person).find((field) => field.name === "address");

		expect(address?.fields?.map((field) => field.name)).toEqual(["street", "city"]);
		expect(address?.fields?.[1]?.path).toEqual(["address", "city"]);
		expect(address?.fields?.[1]?.label).toBe("Town");
	});

	it("are not there for an object the schema says nothing about", () => {
		expect(of(person).find((field) => field.name === "anything")?.fields).toBeUndefined();
	});
});

describe("the order of the fields", () => {
	it("is the schema's, unless x-udl-ui.order says otherwise", () => {
		const found = of({
			type: "object",
			properties: {
				first: { type: "string", "x-udl-ui": { order: 2 } },
				second: { type: "string", "x-udl-ui": { order: 1 } },
				third: { type: "string" },
			},
		});

		// (first asked for 2, where third already is: the declared order keeps first first.)
		expect(found.map((field) => field.name)).toEqual(["second", "first", "third"]);
	});
});

describe("the advanced section", () => {
	it("is where x-udl-ui.advanced says", () => {
		const found = of({
			type: "object",
			properties: {
				plain: { type: "string" },
				rare: { type: "string", "x-udl-ui": { advanced: true } },
			},
		});

		expect(found.map((field) => field.advanced)).toEqual([false, true]);
	});
});

describe("a schema that is not an object with properties", () => {
	it("has no fields", () => {
		expect(of({ type: "string" })).toEqual([]);
		expect(of({})).toEqual([]);
		expect(of(null)).toEqual([]);
		expect(of([1, 2])).toEqual([]);
	});
});

describe("whatever the schema is", () => {
	it("fieldsOf never throws", () => {
		fc.assert(
			fc.property(fc.jsonValue(), (schema) => {
				expect(() => fieldsOf(schema as never)).not.toThrow();
			}),
		);
	});
});

describe("a schema with references", () => {
	const document = {
		components: {
			schemas: {
				Address: {
					type: "object",
					properties: { street: { type: "string" } },
				},
				Person: {
					type: "object",
					properties: { address: { $ref: "#/components/schemas/Address" } },
				},
			},
		},
	};

	it("is read with what its references point at", () => {
		const person = document.components.schemas.Person;

		const [address] = fieldsOf(resolveRefs(person, document));

		expect(address?.fields?.map((field) => field.name)).toEqual(["street"]);
	});

	it("is a box of JSON for a reference it cannot follow", () => {
		const [found] = fieldsOf(
			resolveRefs({ type: "object", properties: { x: { $ref: "#/nope" } } }),
		);

		expect(found?.widget).toBe("json");
	});

	it("is a box of JSON for one that refers to itself", () => {
		const tree = {
			components: {
				schemas: {
					Node: {
						type: "object",
						properties: { child: { $ref: "#/components/schemas/Node" } },
					},
				},
			},
		};

		const [child] = fieldsOf(resolveRefs(tree.components.schemas.Node, tree));

		expect(child?.widget).toBe("json");
	});

	it("keeps what the schema says beside the reference", () => {
		// (pydantic writes a field's own description, default and x-udl-ui next to its $ref,
		// not in an allOf: they are the field's, so they must not be dropped.)
		const found = resolveRefs(
			{
				type: "object",
				properties: {
					street: {
						$ref: "#/components/schemas/Address",
						description: "Where.",
						default: { street: "1 Test Street" },
						"x-udl-ui": { label: "Their street" },
					},
				},
			},
			document,
		);

		expect(found).toEqual({
			type: "object",
			properties: {
				street: {
					type: "object",
					properties: { street: { type: "string" } },
					description: "Where.",
					default: { street: "1 Test Street" },
					"x-udl-ui": { label: "Their street" },
				},
			},
		});
	});

	it("keeps them where the reference is a cycle, too", () => {
		const found = resolveRefs(
			{
				type: "object",
				properties: {
					tree: { $ref: "#/components/schemas/Node", description: "The tree." },
				},
			},
			{
				components: {
					schemas: {
						Node: {
							type: "object",
							properties: { child: { $ref: "#/components/schemas/Node" } },
						},
					},
				},
			},
		);

		const properties = (found as { properties: Record<string, unknown> }).properties;
		expect(properties.tree).toMatchObject({ description: "The tree." });
	});
});

describe("the value of a field", () => {
	it("is read at its path", () => {
		expect(valueAt({ a: { b: 1 } }, ["a", "b"])).toBe(1);
		expect(valueAt({ a: 1 }, ["a", "b"])).toBeUndefined();
	});

	it("is set at its path, the objects on the way made", () => {
		expect(withValue({}, ["a", "b"], 1)).toEqual({ a: { b: 1 } });
		expect(withValue({ a: { b: 1 } }, ["a", "b"], 2)).toEqual({ a: { b: 2 } });
		expect(withValue({ a: { b: 1 } }, ["c"], 3)).toEqual({ a: { b: 1 }, c: 3 });
	});

	it("is taken out of its path, and the objects it leaves stay", () => {
		expect(withoutValue({ a: { b: 1 }, c: 2 }, ["a", "b"])).toEqual({ a: {}, c: 2 });
		expect(withoutValue({ a: 1 }, ["b"])).toEqual({ a: 1 });
	});
});

describe("a list of choices", () => {
	// (pydantic writes `list[Literal["a", "b"]]` with the enum under `items`, never on the array.)
	const schema = {
		type: "object",
		properties: {
			tags: { type: "array", title: "Tags", items: { enum: ["a", "b"] } },
		},
	};

	it("is a multiselect, with its items' choices", () => {
		const tags = of(schema).find((field) => field.name === "tags");

		expect(tags?.widget).toBe("multiselect");
		expect(tags?.options).toEqual([
			{ value: "a", label: "a" },
			{ value: "b", label: "b" },
		]);
	});

	it("says what its items are, too", () => {
		const tags = of(schema).find((field) => field.name === "tags");
		const names = of({
			type: "object",
			properties: { names: { type: "array", title: "Names", items: { type: "string" } } },
		}).find((field) => field.name === "names");

		expect(tags?.items?.options).toHaveLength(2);
		expect(names?.items?.widget).toBe("text");
	});
});
