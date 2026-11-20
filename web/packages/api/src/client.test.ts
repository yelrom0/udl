import { describe, expect, it, vi } from "vitest";
import { createUdlClient, fixtures, type PathParams, UdlError } from "./index";
import { FakeUdl } from "./testing";

/** A fake, and every request the client sent it: its init (which the Request does not keep), and
 * the Request itself (for its URL, headers and body). */
function recording(fake = new FakeUdl()) {
	const inits: (RequestInit | undefined)[] = [];
	const sent: Request[] = [];
	const fetch = (input: RequestInfo | URL, init?: RequestInit) => {
		const request = new Request(input, init);
		inits.push(init);
		sent.push(request.clone());
		return fake.fetch(request);
	};
	return { fake, inits, sent, fetch };
}

/** A fetch that records what it is sent and answers `{}` (for a route the schema does not hold). */
function anything() {
	const sent: Request[] = [];
	const fetch = (input: RequestInfo | URL, init?: RequestInit) => {
		sent.push(new Request(input, init).clone());
		return Promise.resolve(
			new Response("{}", { status: 200, headers: { "Content-Type": "application/json" } }),
		);
	};
	return { sent, fetch };
}

describe("createUdlClient", () => {
	it("sends a read to its route, with no CSRF header and no body", async () => {
		const { fake, sent, fetch } = recording();

		const sites = await createUdlClient({ baseUrl: "http://udl.test", fetch }).call("list_sites");

		expect(sites.sites).toHaveLength(1);
		expect(sent).toHaveLength(1);
		const [request] = sent;
		expect(request?.method).toBe("GET");
		expect(request?.url).toBe("http://udl.test/sites");
		expect(request?.headers.get("X-UDL-CSRF")).toBeNull();
		expect(request?.headers.get("Content-Type")).toBeNull();
		expect(fake.calls.map((call) => call.id)).toEqual(["list_sites"]);
	});

	it("sends the page's cookie with `call`'s request", async () => {
		const { inits, fetch } = recording();

		await createUdlClient({ baseUrl: "http://udl.test", fetch }).call("list_sites");

		expect(inits[0]?.credentials).toBe("same-origin");
	});

	it("sends the page's cookie with the typed client's request too", async () => {
		// (openapi-fetch builds its own Request, and undici's defaults `credentials` to
		// "same-origin", so the Request itself cannot say what was set: the init it was built
		// with does.)
		const built: (RequestInit | undefined)[] = [];
		class RecordingRequest extends Request {
			constructor(input: RequestInfo | URL, init?: RequestInit) {
				super(input, init);
				built.push(init);
			}
		}
		vi.stubGlobal("Request", RecordingRequest);
		try {
			const { fake, fetch } = recording();
			await createUdlClient({ baseUrl: "http://udl.test", fetch }).client.GET("/sites");
			expect(fake.calls.map((call) => call.id)).toEqual(["list_sites"]);
		} finally {
			vi.unstubAllGlobals();
		}

		// (The first is openapi-fetch's; the others are made of the Request it built, and have no
		// init of their own.)
		expect(built[0]?.credentials).toBe("same-origin");
	});

	it("joins the base URL and the route as openapi-fetch does", async () => {
		const urls: string[] = [];
		const fetch = (input: RequestInfo | URL) => {
			urls.push(String(input));
			return Promise.resolve(
				new Response(JSON.stringify({ sites: [] }), {
					headers: { "Content-Type": "application/json" },
				}),
			);
		};

		await createUdlClient({ baseUrl: "", fetch }).call("list_sites");
		await createUdlClient({ baseUrl: "http://host/udl/", fetch }).call("list_sites");
		await createUdlClient({ baseUrl: "http://host/udl", fetch }).call("list_sites");

		// (A page served by the API itself sends to "/sites"; behind a prefix, the prefix stays.)
		expect(urls).toEqual(["/sites", "http://host/udl/sites", "http://host/udl/sites"]);
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

	it("calls an operation named at runtime, as a generated page has it", async () => {
		// (24.6: the operation's name is a string udl sent — `check_account` here, whose route
		// has a path parameter and no body.)
		const { fake, sent, fetch } = recording();
		fake.handle("check_account", () => ({ account: "gmail:work", ok: true }));
		const api = createUdlClient({ baseUrl: "http://udl.test", fetch });

		await api.callNamed("check_account", undefined, { account: "gmail:work" });

		expect(sent[0]?.method).toBe("POST");
		expect(sent[0]?.url).toBe("http://udl.test/accounts/gmail%3Awork/check");
		expect(sent[0]?.headers.get("X-UDL-CSRF")).toBe("1");
	});

	it("calls a plugin's operation by the route udl named", async () => {
		// (A plugin's route is not the API's schema: the fake answers anything it is sent.)
		const { sent, fetch } = anything();
		const api = createUdlClient({ baseUrl: "http://udl.test", fetch });

		await api.callAt("POST", "/integrations/fake/list_things", { account: "fake:work" });

		expect(sent[0]?.method).toBe("POST");
		expect(sent[0]?.url).toBe("http://udl.test/integrations/fake/list_things");
		expect(sent[0]?.headers.get("X-UDL-CSRF")).toBe("1");
		expect(await sent[0]?.json()).toEqual({ account: "fake:work" });
	});

	it("fills a route's path parameters", async () => {
		const { sent, fetch } = anything();
		const api = createUdlClient({ baseUrl: "http://udl.test", fetch });

		await api.callAt("GET", "/integrations/fake/thing/{thing}", undefined, { thing: "t 1" });

		expect(sent[0]?.url).toBe("http://udl.test/integrations/fake/thing/t%201");
		expect(sent[0]?.headers.get("Content-Type")).toBeNull();
	});

	it("sends a read's fields as its query, not its body", async () => {
		// (24.6: a generated page's form for one of udl's own reads — `list_downloads` takes
		// `limit`, `offset`, `site_key` and `status` as query parameters, and `fetch` refuses a
		// body on a GET.)
		const { fake, sent, fetch } = recording();
		fake.handle("list_downloads", () => ({ downloads: [] }));
		const api = createUdlClient({ baseUrl: "http://udl.test", fetch });

		await api.callNamed("list_downloads", { limit: 5, status: ["done", "failed"] });

		expect(sent[0]?.method).toBe("GET");
		expect(sent[0]?.url).toBe("http://udl.test/downloads?limit=5&status=done&status=failed");
		expect(sent[0]?.headers.get("Content-Type")).toBeNull();
	});

	it("refuses a name that is no operation", async () => {
		const api = createUdlClient({ baseUrl: "http://udl.test", fetch: recording().fetch });

		expect(() => api.callNamed("no_such_operation")).toThrow(/no operation/);
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
