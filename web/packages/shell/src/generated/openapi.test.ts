/**
 * -------------------
 * - openapi.test.ts -
 * -------------------
 *
 * 24.4's Done-when: every request in the API's own `openapi.json` draws a form — no request is a
 * single box of JSON, which would mean the schema said nothing a form could show. It is the
 * committed schema the CLI, the API and MCP all offer, so a request that stops being a form is a
 * regression these tests catch.
 */

import { readFileSync } from "node:fs";
import { join } from "node:path";
import { type FieldSpec, fieldsOf, isJsonObject, type JsonValue, resolveRefs } from "@udl/ui-sdk";
import { describe, expect, it } from "vitest";

// (The tests run from this package's directory: the API's schema is its sibling's.)
const OPENAPI: JsonValue = JSON.parse(readFileSync(join("..", "api", "openapi.json"), "utf8"));

function object(value: JsonValue | undefined, what: string): Readonly<Record<string, JsonValue>> {
	if (!isJsonObject(value)) {
		throw new Error(`openapi.json: ${what} is not an object`);
	}
	return value;
}

/** Every operation's request body's schema, by the operation's id. */
function requests(): ReadonlyMap<string, JsonValue> {
	const found = new Map<string, JsonValue>();
	for (const [path, item] of Object.entries(object(OPENAPI, "the document"))) {
		if (path !== "paths") {
			continue;
		}
		for (const [route, methods] of Object.entries(object(item, "paths"))) {
			for (const [method, operation] of Object.entries(object(methods, route))) {
				const body = object(operation, `${method} ${route}`).requestBody;
				if (!isJsonObject(body)) {
					continue;
				}
				const content = object(body.content, "content");
				const json = object(content["application/json"] ?? {}, "application/json");
				const schema = json.schema;
				if (schema === undefined) {
					continue;
				}
				found.set(`${method} ${route}`, schema);
			}
		}
	}
	return found;
}

/** Whether a field would show a box of JSON (rather than a control that says what it is). */
function isJsonBox(field: FieldSpec): boolean {
	return field.widget === "json" && (field.fields === undefined || field.fields.length === 0);
}

describe("every request in openapi.json", () => {
	const found = requests();

	it("is there to check (the schema is the API's)", () => {
		expect(found.size).toBeGreaterThan(20);
	});

	it("draws a form, not one box of JSON", () => {
		const boxes = [...found]
			.map(([name, schema]) => [name, fieldsOf(resolveRefs(schema, OPENAPI))] as const)
			.filter(([, fields]) => fields.length > 0 && fields.every(isJsonBox))
			.map(([name]) => name)
			.sort();

		// (These two are what a box of JSON is for: a list of strings, and the settings' own
		// values. Every other request has a field a control says something about — a nullable
		// string is still a text box, which is the case a schema-only reader gets wrong.)
		expect(boxes).toEqual(["post /fetches/challenges/solve", "put /plugins/{name}/settings"]);
	});

	it("names every field it has", () => {
		for (const [name, schema] of found) {
			for (const field of fieldsOf(resolveRefs(schema, OPENAPI))) {
				expect([name, field.label.length > 0]).toEqual([name, true]);
			}
		}
	});
});
