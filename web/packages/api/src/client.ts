import createClient, { type Client, type Middleware } from "openapi-fetch";
import { type OperationId, operations } from "./operations";
import type { components, operations as Operations, paths } from "./schema";

/** The typed client (openapi-fetch's): every route, with the request and answer types the
 * schema gives it. `call` names an operation by its operationId; this reaches what a body and
 * path parameters do not describe (a stream, a query with a list, a request that is not JSON). */
export type UdlClient = Client<paths>;

/** udl's answer to a request that failed (`ErrorInfo`): what went wrong, and what to do. */
export type ErrorInfo = components["schemas"]["ErrorInfo"];

/** A request udl refused, or could not answer: the `ErrorInfo` it said, and its status. */
export class UdlError extends Error {
	readonly info: ErrorInfo;
	readonly status: number;

	constructor(info: ErrorInfo, status: number) {
		super(info.message);
		this.name = "UdlError";
		this.info = info;
		this.status = status;
	}
}

// (`never` is what the generated types say for a request, an answer or parameters that are not
// there; `[T] extends [never]` is how a conditional type asks, since `never` extends everything.)
type JsonBody<T> = [NonNullable<T>] extends [never]
	? undefined
	: NonNullable<T> extends { content: { "application/json": infer Body } }
		? Body
		: undefined;

/** The JSON a request's body is made of (`undefined` for the operations that take none). */
export type RequestBody<Id extends OperationId> = JsonBody<Operations[Id]["requestBody"]>;

/** The path parameters an operation's route has (`never` for the routes without one). */
export type PathParams<Id extends OperationId> = NonNullable<Operations[Id]["parameters"]["path"]>;

/** The status udl answers a successful request with (the first of its 2xx ones). */
type SuccessStatus<Id extends OperationId> = keyof Operations[Id]["responses"] &
	(200 | 201 | 202 | 204);

/** What a successful request answers: its JSON body (`undefined` when it has none, as a
 * delete's 204 does). */
export type ResponseBody<Id extends OperationId> = JsonBody<
	Operations[Id]["responses"][SuccessStatus<Id>]
>;

/** Where the client sends its requests, and the `fetch` it sends them with (the page's own,
 * unless a test passes one). */
export interface UdlClientOptions {
	baseUrl: string;
	fetch?: typeof globalThis.fetch;
}

/** udl's API: the typed client, and `call`, which names an operation by its operationId. */
export interface UdlApi {
	readonly client: UdlClient;
	call<Id extends OperationId>(
		id: Id,
		body?: RequestBody<Id>,
		params?: PathParams<Id>,
	): Promise<ResponseBody<Id>>;
	/** `call` for a name the page has at runtime only — a generated page's operation (24.6),
	 * which udl named in its JSON — so the request is JSON the schema cannot type. A name that
	 * is no operation throws (udl's JSON is checked before a page is drawn from it). */
	callNamed(id: string, body?: unknown, params?: Record<string, string>): Promise<unknown>;
	/** `call` for a route the page has at runtime only — a plugin's operation (24.6), whose
	 * method and path udl's JSON names — so the request is JSON the schema cannot type. */
	callAt(
		method: string,
		path: string,
		body?: unknown,
		params?: Readonly<Record<string, unknown>>,
	): Promise<unknown>;
}

// (The API takes a write from a page only with this header: a write from another site's page is
// refused. See the API's module docstring.)
const WRITES = new Set(["POST", "PUT", "PATCH", "DELETE"]);

// (A read's request is its query, not its body: `fetch` refuses a body on a GET, and the API
// takes a read's fields as query parameters — `limit`, `status`, `max_chars` and the rest.)
const QUERIES = new Set(["GET", "HEAD", "DELETE"]);

const csrf: Middleware = {
	onRequest({ request }) {
		if (WRITES.has(request.method)) request.headers.set("X-UDL-CSRF", "1");
		return request;
	},
};

/** A client of udl's API at `baseUrl`, signed in by the page's session cookie (same-origin). */
export function createUdlClient({ baseUrl, fetch }: UdlClientOptions): UdlApi {
	const client = createClient<paths>({
		baseUrl,
		credentials: "same-origin",
		...(fetch ? { fetch } : {}),
	});
	client.use(csrf);
	const doFetch = fetch ?? globalThis.fetch;
	return {
		client,
		call: (id, body, params) => send(doFetch, baseUrl, id, body, params),
		callNamed: (id, body, params) => {
			if (!isOperationId(id)) {
				throw new Error(`no operation named ${id}`);
			}
			return send(doFetch, baseUrl, id, body, params);
		},
		callAt: (method, path, body, params) => sendTo(doFetch, baseUrl, method, path, body, params),
	};
}

/** Whether `name` is one of the API's operations (`operations` is the schema's own list). */
function isOperationId(name: string): name is OperationId {
	return Object.hasOwn(operations, name);
}

async function send<Id extends OperationId>(
	doFetch: typeof globalThis.fetch,
	baseUrl: string,
	id: Id,
	// (What `call` gives it, typed by the operation, and what `callNamed` has at runtime only: a
	// generated page's form, which is JSON the schema cannot see. The route and the method are
	// the operation's either way.)
	body?: unknown,
	params?: Readonly<Record<string, unknown>>,
): Promise<ResponseBody<Id>> {
	const { method, path } = operations[id];
	// (The schema is what says a 2xx answer's body is this operation's `ResponseBody`: the one
	// place where the types and the JSON meet.)
	return (await sendTo(doFetch, baseUrl, method, path, body, params)) as ResponseBody<Id>;
}

/** Send a request to `path` (with its `{name}` placeholders filled from `params`), as `call`
 * does, for a route the schema does not hold. */
async function sendTo(
	doFetch: typeof globalThis.fetch,
	baseUrl: string,
	method: string,
	path: string,
	body?: unknown,
	params?: Readonly<Record<string, unknown>>,
): Promise<unknown> {
	const verb = method.toUpperCase();
	const asQuery = QUERIES.has(verb);
	const init: RequestInit = {
		method: verb,
		credentials: "same-origin",
		headers: headersFor(verb, body !== undefined && !asQuery),
	};
	let route = fill(path, params);
	if (body !== undefined) {
		if (asQuery) {
			const query = searchOf(body);
			if (query !== "") route += `${route.includes("?") ? "&" : "?"}${query}`;
		} else {
			init.body = JSON.stringify(body);
		}
	}
	// (Joined as openapi-fetch joins it: a base that is a page's own origin is "", and one behind
	// a reverse proxy's prefix keeps it.)
	const response = await doFetch(`${baseUrl.replace(/\/+$/, "")}${route}`, init);
	const data: unknown = parse(response.status === 204 ? "" : await response.text());
	if (!response.ok) throw new UdlError(asErrorInfo(data), response.status);
	return data;
}

/** The route `path`, with its `{name}` placeholders replaced by `params` (and encoded). */
function fill(path: string, params: Readonly<Record<string, unknown>> | undefined): string {
	const values = new Map(
		Object.entries(params ?? {}).map(([name, value]) => [name, String(value)]),
	);
	return path.replace(/\{(\w+)\}/g, (_, name: string) => {
		const value = values.get(name);
		if (value === undefined) throw new Error(`${path} needs a path parameter: ${name}`);
		return encodeURIComponent(value);
	});
}

/** `body` as a query string: a read's fields, a list repeated (as FastAPI takes one). */
function searchOf(body: unknown): string {
	if (typeof body !== "object" || body === null) return "";
	const query = new URLSearchParams();
	for (const [field, value] of Object.entries(body)) {
		for (const one of Array.isArray(value) ? value : [value]) {
			if (one === undefined) continue;
			query.append(field, one === null ? "" : String(one));
		}
	}
	return query.toString();
}

function headersFor(method: string, hasBody: boolean): Record<string, string> {
	const headers: Record<string, string> = {};
	if (hasBody) headers["Content-Type"] = "application/json";
	if (WRITES.has(method.toUpperCase())) headers["X-UDL-CSRF"] = "1";
	return headers;
}

/** `text` as the JSON it is (`undefined` when it is not JSON, or is empty: a status that is not
 * udl's may answer a page, and a 204 answers nothing). */
function parse(text: string): unknown {
	if (text === "") return undefined;
	try {
		return JSON.parse(text);
	} catch {
		return undefined;
	}
}

/** `value` as an `ErrorInfo`: what udl answered, or — a status that is not udl's — one made
 * from the status alone. */
function asErrorInfo(value: unknown): ErrorInfo {
	if (isErrorInfo(value)) return value;
	return {
		code: "udl_error",
		message: "udl answered with a status it did not explain",
		hint: null,
		retry_after: null,
		tool: null,
	};
}

function isErrorInfo(value: unknown): value is ErrorInfo {
	if (typeof value !== "object" || value === null) return false;
	return (
		"code" in value &&
		"message" in value &&
		typeof value.code === "string" &&
		typeof value.message === "string"
	);
}
