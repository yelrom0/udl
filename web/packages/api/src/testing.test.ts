import { describe, expect, it } from "vitest";
import { fixtures, type UdlApi } from "./index";
import { FakeUdl } from "./testing";

/** A client on the fake, and the fake (to say what it was asked). */
function fake(): { fake: FakeUdl; api: UdlApi } {
	const udl = new FakeUdl();
	return { fake: udl, api: udl.api() };
}

describe("FakeUdl", () => {
	it("answers each list with the schema's fixture", async () => {
		const { api } = fake();

		expect(await api.call("list_sites")).toEqual(fixtures.list_sites);
		expect(await api.call("list_downloads")).toEqual(fixtures.list_downloads);
		expect(await api.call("list_plugins")).toEqual(fixtures.list_plugins);
	});

	it("answers with a handler's answer instead", async () => {
		const { fake: udl, api } = fake();
		udl.handle("list_sites", () => ({ sites: [] }));

		expect(await api.call("list_sites")).toEqual({ sites: [] });
	});

	it("gives a handler the body and the path parameters", async () => {
		const { fake: udl, api } = fake();
		const seen: { id: string }[] = [];
		udl.handle("cancel_download", ({ params }) => {
			seen.push(params);
			return fixtures.list_downloads;
		});

		await api.call("cancel_download", undefined, { id: "a b/c" });

		expect(seen).toEqual([{ id: "a b/c" }]);
	});

	it("records every call, with what it was sent", async () => {
		const { fake: udl, api } = fake();
		udl.handle("cancel_download", () => fixtures.list_downloads);

		await api.call("list_sites");
		await api.call("cancel_download", undefined, { id: "abc" });

		expect(udl.calls).toEqual([
			{ id: "list_sites", body: undefined, params: {} },
			{ id: "cancel_download", body: undefined, params: { id: "abc" } },
		]);
	});

	it("records a write's body", async () => {
		const { fake: udl, api } = fake();
		udl.handle("probe_url", ({ body }) => ({
			url: body.url,
			site_key: "example.com",
			signed_in: false,
			results: [],
		}));

		const report = await api.call("probe_url", { url: "https://example.com", all_tools: false });

		expect(report.url).toBe("https://example.com");
		expect(udl.calls[0]?.body).toEqual({ url: "https://example.com", all_tools: false });
	});

	it("says so when it has no answer for an operation", async () => {
		const { api } = fake();

		await expect(api.call("get_status")).rejects.toThrow(/get_status/);
	});

	it("says so when a route is not the API's", async () => {
		const udl = new FakeUdl();

		await expect(udl.fetch("http://udl.test/nowhere")).rejects.toThrow(/GET \/nowhere/);
		await expect(udl.fetch("http://udl.test/sites", { method: "PATCH" })).rejects.toThrow(
			/PATCH \/sites/,
		);
	});
});
