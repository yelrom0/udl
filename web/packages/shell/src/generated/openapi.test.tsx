/**
 * ---------------------
 * - openapi.test.tsx -
 * ---------------------
 *
 * 24.5's Done-when: `list_accounts`' and `list_events`' responses render as tables — the API's
 * own committed schemas and the fixtures the shell's fake API answers with, not a schema written
 * to fit the component. A response that stops being a table is a regression these tests catch.
 */

import { readFileSync } from "node:fs";
import { join } from "node:path";
import { render, screen, within } from "@testing-library/react";
import { fixtures } from "@udl/api";
import type { JsonValue } from "@udl/ui-sdk";
import { describe, expect, it } from "vitest";
import { ResultView } from "./ResultView";
import { shapeOf } from "./result";

// (The tests run from this package's directory: the API's schema is its sibling's.)
const OPENAPI: JsonValue = JSON.parse(readFileSync(join("..", "api", "openapi.json"), "utf8"));

/** The response schema of the operation `name`, from the API's OpenAPI document. */
function responseOf(name: string): JsonValue {
	const document = OPENAPI as Readonly<Record<string, JsonValue>>;
	const paths = document.paths;
	if (typeof paths !== "object" || paths === null || Array.isArray(paths)) {
		throw new Error("openapi.json has no paths");
	}
	for (const methods of Object.values(paths)) {
		if (typeof methods !== "object" || methods === null || Array.isArray(methods)) {
			continue;
		}
		for (const operation of Object.values(methods)) {
			if (typeof operation !== "object" || operation === null || Array.isArray(operation)) {
				continue;
			}
			const id = operation.operationId;
			if (id !== name) {
				continue;
			}
			const responses = operation.responses;
			if (typeof responses !== "object" || responses === null || Array.isArray(responses)) {
				throw new Error(`${name} has no responses`);
			}
			const ok = responses["200"];
			if (typeof ok !== "object" || ok === null || Array.isArray(ok)) {
				throw new Error(`${name} has no 200`);
			}
			const content = ok.content;
			if (typeof content !== "object" || content === null || Array.isArray(content)) {
				throw new Error(`${name} has no content`);
			}
			const json = content["application/json"];
			if (typeof json !== "object" || json === null || Array.isArray(json)) {
				throw new Error(`${name} has no application/json`);
			}
			return json.schema ?? {};
		}
	}
	throw new Error(`openapi.json has no operation ${name}`);
}

describe("list_accounts", () => {
	const schema = responseOf("list_accounts");
	const value = fixtures.list_accounts as JsonValue;

	it("is a table, its columns the account's scalars", () => {
		const shape = shapeOf(schema, value, OPENAPI);

		expect(shape.kind).toBe("table");
		if (shape.kind !== "table") {
			throw new Error("not a table");
		}
		expect(shape.columns.map((column) => column.name)).toEqual([
			"account",
			"connected_at",
			"created_at",
			"enabled",
			"id",
			"integration",
			"method",
			"name",
			"pending_until",
			"status",
		]);
	});

	it("renders as one, with a row per account", () => {
		render(<ResultView schema={schema} value={value} document={OPENAPI} />);

		const table = screen.getByRole("table");
		expect(within(table).getAllByRole("row").length).toBeGreaterThan(1);
		expect(within(table).getByText("Integration")).toBeDefined();
	});
});

describe("list_events", () => {
	const schema = responseOf("list_events");
	const value = fixtures.list_events as JsonValue;

	it("is a table of the events", () => {
		const shape = shapeOf(schema, value, OPENAPI);

		expect(shape.kind).toBe("table");
		if (shape.kind !== "table") {
			throw new Error("not a table");
		}
		expect(shape.columns.map((column) => column.name)).toEqual([
			"account_id",
			"caused_by",
			"dedupe_key",
			"depth",
			"dispatched_at",
			"dropped_reason",
			"id",
			"occurred_at",
			"source",
			"status",
			"stored_at",
			"subject",
			"type",
		]);
	});

	it("renders as one", () => {
		render(<ResultView schema={schema} value={value} document={OPENAPI} />);

		expect(screen.getByRole("table")).toBeDefined();
	});
});

describe("github_list_pulls (11.4's fan-out)", () => {
	// (Its response is `FanOutResult[PullInfo]`: an account's pulls, each with its account.)
	const schema = responseOf("github_list_pulls");
	const value = {
		items: [
			{
				account: "github:work",
				item: {
					author: "someone",
					base: "main",
					draft: false,
					head: "feat/x",
					head_sha: "abc123",
					html_url: "https://github.com/yelrom0/udl/pull/1",
					merged: false,
					number: 1,
					repo: "yelrom0/udl",
					state: "open",
					title: "A pull",
					updated_at: "2026-11-19T00:00:00Z",
				},
			},
		],
		failures: [],
		next_pages: {},
	} as JsonValue;

	it("is a table of the pulls, with their account first", () => {
		const shape = shapeOf(schema, value, OPENAPI);

		expect(shape.kind).toBe("table");
		if (shape.kind !== "table") {
			throw new Error("not a table");
		}
		expect(shape.columns.map((column) => column.name)).toEqual([
			"account",
			"author",
			"base",
			"draft",
			"head",
			"head_sha",
			"html_url",
			"merged",
			"number",
			"repo",
			"state",
			"title",
			"updated_at",
		]);
		expect(shape.rows).toEqual([
			{ ...(value as { items: [{ item: Row }] }).items[0].item, account: "github:work" },
		]);
	});

	it("renders as one", () => {
		render(<ResultView schema={schema} value={value} document={OPENAPI} />);

		const table = screen.getByRole("table");
		expect(within(table).getByText("github:work")).toBeDefined();
		expect(within(table).getByText("A pull")).toBeDefined();
	});
});
