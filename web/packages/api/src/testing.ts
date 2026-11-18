import {
	createUdlClient,
	type PathParams,
	type RequestBody,
	type ResponseBody,
	type UdlApi,
} from "./client";
import { fixtures } from "./fixtures";
import type { OperationId } from "./operations";
import { operations } from "./operations";

/** What a handler is given: the request's JSON body and its path parameters. */
export interface FakeRequest<Id extends OperationId> {
	readonly body: RequestBody<Id>;
	readonly params: PathParams<Id>;
}

/** A request the fake answered: which operation, and what it was sent. */
export interface FakeCall {
	readonly id: OperationId;
	readonly body: unknown;
	readonly params: Record<string, string>;
}

type Handler = (request: { body: unknown; params: Record<string, string> }) => unknown;

/** udl's API, in memory: an answer to every `list_…` operation (from the schema's fixtures),
 * and `handle` for the ones a test cares about. It records every call, so a test can say what
 * the page asked for. */
export class FakeUdl {
	private readonly handlers = new Map<string, Handler>();
	/** Every request the fake answered, in order. */
	readonly calls: FakeCall[] = [];

	constructor() {
		for (const [id, fixture] of Object.entries(fixtures)) {
			this.handlers.set(id, () => fixture);
		}
	}

	/** Answer `id` with `handler`'s answer (`undefined` for a 204), instead of its fixture. */
	handle<Id extends OperationId>(
		id: Id,
		handler: (request: FakeRequest<Id>) => ResponseBody<Id> | Promise<ResponseBody<Id>>,
	): this {
		// (The map holds handlers of every operation, so it cannot be typed by one; `handle`
		// is where a test's handler meets the operation it is for.)
		this.handlers.set(id, handler as Handler);
		return this;
	}

	/** A client on this fake, as `createUdlClient({ baseUrl, fetch: fake.fetch })` would make. */
	api(baseUrl = "http://udl.test"): UdlApi {
		return createUdlClient({ baseUrl, fetch: this.fetch });
	}

	/** The `fetch` every client of this fake sends its requests with. */
	readonly fetch: typeof globalThis.fetch = async (input, init) => {
		const request = new Request(input, init);
		const { id, params } = this.route(request);
		const text = await request.text();
		const body: unknown = text === "" ? undefined : JSON.parse(text);
		this.calls.push({ id, body, params });
		const handler = this.handlers.get(id);
		if (!handler) throw new Error(`FakeUdl has no handler for ${id}`);
		const answer = await handler({ body, params });
		return new Response(JSON.stringify(answer), {
			status: 200,
			headers: { "Content-Type": "application/json" },
		});
	};

	private route(request: Request): { id: OperationId; params: Record<string, string> } {
		const { pathname } = new URL(request.url);
		for (const id of Object.keys(operations) as OperationId[]) {
			const { method, path } = operations[id];
			if (method.toUpperCase() !== request.method) continue;
			const params = match(path, pathname);
			if (params) return { id, params };
		}
		throw new Error(`FakeUdl knows no ${request.method} ${pathname}`);
	}
}

/** The path parameters `template` takes from `pathname` (`null` when it does not match). */
function match(template: string, pathname: string): Record<string, string> | null {
	const names: string[] = [];
	const pattern = template.replace(/\{(\w+)\}/g, (_, name: string) => {
		names.push(name);
		return "([^/]+)";
	});
	const found = new RegExp(`^${pattern}$`).exec(pathname);
	if (!found) return null;
	return Object.fromEntries(names.map((name, i) => [name, decodeURIComponent(found[i + 1] ?? "")]));
}
