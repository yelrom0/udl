/**
 * `ctx.call(name, request)`: any of udl's operations, by the name its operationId has, so a
 * plugin reaches the operations its own Python plugin added without the shell knowing them.
 */

import { describe, expect, it } from "vitest";
import { callOperation } from "./call";

/** What a `fetch` was asked, and the answer it gives. */
interface Asked {
	readonly url: string;
	readonly init: RequestInit | undefined;
}

/** A `fetch` that answers `body` (as JSON) with `status`, and records what it was asked. */
function answering(body: unknown, status = 200) {
	const asked: Asked[] = [];
	const fetch = async (input: RequestInfo | URL, init?: RequestInit): Promise<Response> => {
		asked.push({ url: String(input), init });
		return new Response(JSON.stringify(body), { status });
	};
	return { fetch, asked };
}

describe("calling an operation by name", () => {
	it("fills a path parameter from the request, and the rest into the query", async () => {
		const { fetch, asked } = answering({ id: "abc", url: "https://example.test/" });

		const answer = await callOperation("get_download", { id: "abc", verbose: "1" }, fetch);

		expect(asked[0]?.url).toBe("/downloads/abc?verbose=1");
		expect(answer).toEqual({ id: "abc", url: "https://example.test/" });
	});

	it("sends what is left of a write's request as its JSON body, with the CSRF header", async () => {
		const { fetch, asked } = answering({ id: "abc" }, 202);

		await callOperation("download", { url: "https://example.test/", format: "md" }, fetch);

		const [first] = asked;
		expect(first?.url).toBe("/downloads");
		expect(first?.init?.method).toBe("POST");
		expect(first?.init?.body).toBe(JSON.stringify({ url: "https://example.test/", format: "md" }));
		expect(new Headers(first?.init?.headers).get("X-UDL-CSRF")).toBe("1");
		expect(new Headers(first?.init?.headers).get("Content-Type")).toBe("application/json");
	});

	it("sends an empty body for a write whose request has no fields", async () => {
		// (udl's routes take their request as the body, and FastAPI says a body is missing when
		// there is none: an operation whose request is all-optional — `orch_inbox` — is asked
		// with `{}`, not with nothing.)
		const { fetch, asked } = answering({ project: "udl" });

		await callOperation("orch_inbox", {}, fetch);

		const [first] = asked;
		expect(first?.url).toBe("/plugins/orch/inbox");
		expect(first?.init?.method).toBe("POST");
		expect(first?.init?.body).toBe("{}");
		expect(new Headers(first?.init?.headers).get("Content-Type")).toBe("application/json");
	});

	it("takes no body for a write that has none", async () => {
		const { fetch, asked } = answering(undefined, 204);

		await callOperation("cancel_download", { id: "abc" }, fetch);

		const [first] = asked;
		expect(first?.url).toBe("/downloads/abc");
		expect(first?.init?.method).toBe("DELETE");
		expect(first?.init?.body).toBeUndefined();
		// (A delete writes: it needs the CSRF header, even with no body.)
		expect(new Headers(first?.init?.headers).get("X-UDL-CSRF")).toBe("1");
	});

	it("answers nothing for a request udl answers with nothing", async () => {
		const fetch = async (): Promise<Response> => new Response(null, { status: 204 });

		expect(await callOperation("cancel_run", { id: "abc" }, fetch)).toBeNull();
	});

	it("sends a list as its own name again, and an object as its JSON", async () => {
		const { fetch, asked } = answering({ events: [] });

		await callOperation("list_events", { type: ["a", "b"], filter: { k: 1 }, empty: null }, fetch);

		expect(asked[0]?.url).toBe("/events?type=a&type=b&filter=%7B%22k%22%3A1%7D");
	});

	it("asks a read with no query at all when its request is empty", async () => {
		const { fetch, asked } = answering({});

		await callOperation("get_status", {}, fetch);

		expect(asked[0]?.url).toBe("/status");
	});

	it("refuses a name udl has no operation for", async () => {
		const { fetch, asked } = answering({});

		await expect(callOperation("make_coffee", {}, fetch)).rejects.toThrow(
			'udl has no operation "make_coffee"',
		);
		expect(asked).toEqual([]);
	});

	it("says which parameter a request is missing", async () => {
		const { fetch } = answering({});

		await expect(callOperation("get_download", {}, fetch)).rejects.toThrow(
			'get_download needs its "id"',
		);
	});

	it("says what udl refused a request with, and its hint", async () => {
		const { fetch } = answering(
			{ code: "not_found", message: "no download abc", hint: "list them first" },
			404,
		);

		await expect(callOperation("get_download", { id: "abc" }, fetch)).rejects.toThrow(
			"no download abc (list them first)",
		);
	});

	it("says what udl answered when it was not its own error", async () => {
		const { fetch } = answering("<html>bad gateway</html>", 502);

		await expect(callOperation("get_status", {}, fetch)).rejects.toThrow("udl answered 502");
	});
});
