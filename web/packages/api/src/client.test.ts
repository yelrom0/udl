import { describe, expect, it } from "vitest";
import { createUdlClient, fixtures, type PathParams, UdlError } from "./index";
import { FakeUdl } from "./testing";

/** A fake, and every request the client sent it (to say what it said). */
function recording(fake = new FakeUdl()) {
	const sent: Request[] = [];
	const fetch = (input: RequestInfo | URL, init?: RequestInit) => {
		const request = new Request(input, init);
		sent.push(request.clone());
		return fake.fetch(request);
	};
	return { fake, sent, fetch };
}

describe("createUdlClient", () => {
	it("sends a read to its route, with the page's cookie and no CSRF header", async () => {
		const { fake, sent, fetch } = recording();

		const sites = await createUdlClient({ baseUrl: "http://udl.test", fetch }).call("list_sites");

		expect(sites.sites).toHaveLength(1);
		expect(sent).toHaveLength(1);
		const [request] = sent;
		expect(request?.method).toBe("GET");
		expect(request?.url).toBe("http://udl.test/sites");
		expect(request?.credentials).toBe("same-origin");
		expect(request?.headers.get("X-UDL-CSRF")).toBeNull();
		expect(request?.headers.get("Content-Type")).toBeNull();
		expect(fake.calls.map((call) => call.id)).toEqual(["list_sites"]);
	});

	it("sends a write with the CSRF header and the body as JSON", async () => {
		const { fake, sent, fetch } = recording();
		fake.handle("probe_url", ({ body }) => ({
			url: body.url,
			site_key: "example.com",
			signed_in: false,
			results: [],
		}));
		const api = createUdlClient({ baseUrl: "http://udl.test", fetch });

		await api.call("probe_url", { url: "https://example.com/v", all_tools: false });

		const [request] = sent;
		expect(request?.method).toBe("POST");
		expect(request?.headers.get("X-UDL-CSRF")).toBe("1");
		expect(request?.headers.get("Content-Type")).toBe("application/json");
		expect(await request?.json()).toEqual({ url: "https://example.com/v", all_tools: false });
	});

	it("answers undefined for a 204", async () => {
		// (No route answers 204 today; a sign-out will. What udl answers is JSON.)
		const fetch: typeof globalThis.fetch = async () => new Response(null, { status: 204 });
		const api = createUdlClient({ baseUrl: "http://udl.test", fetch });

		await expect(
			api.call("delete_site", undefined, { key: "example.com" }),
		).resolves.toBeUndefined();
	});

	it("answers a UdlError carrying the ErrorInfo and the status", async () => {
		const fetch: typeof globalThis.fetch = async () =>
			new Response(
				JSON.stringify({ code: "does_not_exist", message: "No site 'x'", hint: "list them" }),
				{ status: 404, headers: { "Content-Type": "application/json" } },
			);
		const api = createUdlClient({ baseUrl: "http://udl.test", fetch });

		const error = await api.call("get_site", undefined, { key: "x" }).catch((e: unknown) => e);

		expect(error).toBeInstanceOf(UdlError);
		const udlError = error as UdlError;
		expect(udlError.status).toBe(404);
		expect(udlError.info).toEqual({
			code: "does_not_exist",
			message: "No site 'x'",
			hint: "list them",
		});
		expect(udlError.message).toBe("No site 'x'");
	});

	it("makes an ErrorInfo of a status that is not udl's", async () => {
		const fetch: typeof globalThis.fetch = async () => new Response("<html>", { status: 502 });
		const api = createUdlClient({ baseUrl: "http://udl.test", fetch });

		const error = await api.call("list_sites").catch((e: unknown) => e);

		expect(error).toBeInstanceOf(UdlError);
		expect((error as UdlError).info.code).toBe("udl_error");
		expect((error as UdlError).status).toBe(502);
	});

	it("fills a route's path parameters, encoded", async () => {
		const { fake, sent, fetch } = recording();
		fake.handle("cancel_download", () => fixtures.list_downloads);

		await createUdlClient({ baseUrl: "http://udl.test", fetch }).call(
			"cancel_download",
			undefined,
			{ id: "a b/c" },
		);

		expect(sent[0]?.url).toBe("http://udl.test/downloads/a%20b%2Fc");
	});

	it("refuses a route whose path parameter it was not given", async () => {
		const { fetch } = recording();
		const api = createUdlClient({ baseUrl: "http://udl.test", fetch });

		// (The types require the parameter; this is the runtime guard behind them.)
		await expect(
			api.call("cancel_download", undefined, {} as PathParams<"cancel_download">),
		).rejects.toThrow(/id/);
	});

	it("sends nothing for a body it was not given", async () => {
		const { sent, fetch } = recording();

		await createUdlClient({ baseUrl: "http://udl.test", fetch }).call("list_sites");

		expect(await sent[0]?.text()).toBe("");
	});

	it("reaches the rest of the API through the typed client", async () => {
		const { fake, fetch } = recording();
		const api = createUdlClient({ baseUrl: "http://udl.test", fetch });

		const { data, error } = await api.client.GET("/sites", {
			params: { query: { limit: 5 } },
		});

		expect(error).toBeUndefined();
		expect(data?.sites).toHaveLength(1);
		expect(fake.calls.map((call) => call.id)).toEqual(["list_sites"]);
	});

	it("sends the CSRF header for a write through the typed client too", async () => {
		const { fake, sent, fetch } = recording();
		fake.handle("probe_url", ({ body }) => ({
			url: body.url,
			site_key: "example.com",
			signed_in: false,
			results: [],
		}));
		const api = createUdlClient({ baseUrl: "http://udl.test", fetch });

		await api.client.POST("/probe", { body: { url: "https://example.com", all_tools: false } });

		expect(sent[0]?.headers.get("X-UDL-CSRF")).toBe("1");
	});

	it("uses the page's own fetch when it is given none", async () => {
		const original = globalThis.fetch;
		const { fetch } = recording();
		globalThis.fetch = fetch;
		try {
			await createUdlClient({ baseUrl: "http://udl.test" }).call("list_sites");
		} finally {
			globalThis.fetch = original;
		}
	});
});
