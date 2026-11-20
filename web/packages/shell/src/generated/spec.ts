/**
 * ----------
 * - spec.ts -
 * ----------
 *
 * What `GET /ui/generated.json` answers (24.3), as the page reads it: every plugin's operations
 * with the view each opens in and the schemas its form and table are drawn from, its settings
 * form, how its accounts connect, and the event types it publishes. udl's own operations are
 * under the pseudo-plugin `udl`.
 *
 * It is not the OpenAPI document (it is the page's own, and it is only what a page needs), so
 * the types are written here, and `generatedUI()` checks what udl answered before a page draws
 * it: a field the page does not understand is a page that does not open.
 */

import type { JsonValue } from "@udl/ui-sdk";

/** Which view an operation opens in (`auto` is already resolved by udl). */
export const VIEWS = ["list", "detail", "form", "action"] as const;
export type View = (typeof VIEWS)[number];

/** What an operation does to the world (the API's `OperationHints`). */
export interface GeneratedHints {
	readonly read_only: boolean;
	readonly destructive: boolean;
	readonly idempotent: boolean;
	readonly open_world: boolean;
	readonly admin: boolean;
	readonly mcp: boolean;
}

/** How the page shows an operation (the API's `UISpec`, with `view` resolved). */
export interface GeneratedUISpec {
	readonly group: string | null;
	readonly order: number;
	readonly view: View;
	readonly icon: string | null;
	readonly hidden: boolean;
	/** A request field that must equal a named thing before the button enables. */
	readonly confirm_field: string | null;
}

/** One operation, as the page draws it. */
export interface GeneratedOperation {
	/** Its name (the API's operationId). */
	readonly name: string;
	readonly title: string;
	readonly summary: string;
	readonly description: string;
	readonly hints: GeneratedHints;
	readonly ui: GeneratedUISpec;
	/** Its route's method and path (`null` for udl's own: the page names those by operation). */
	readonly method: string | null;
	readonly path: string | null;
	/** Its request's JSON Schema, `$defs` inlined: the form is drawn from it. */
	readonly request_schema: JsonValue;
	/** Its response's JSON Schema, `$defs` inlined: the table is drawn from it. */
	readonly response_schema: JsonValue;
}

/** A plugin's settings, as a form. */
export interface GeneratedForm {
	readonly json_schema: JsonValue;
	/** The fields only the config file sets. */
	readonly read_only: readonly string[];
	/** The fields the secret store keeps. */
	readonly secret: readonly string[];
}

/** How an integration's accounts connect. */
export interface GeneratedAccounts {
	/** How they connect, the usual first (`oauth`, `tokens`, `none`). */
	readonly connect: readonly string[];
	/** What an account may be connected for, the default first. */
	readonly access_levels: readonly string[];
	readonly options_schema: JsonValue;
}

/** One plugin's page. */
export interface GeneratedPlugin {
	readonly name: string;
	/** Its kind (its entry point group), `null` for `udl`. */
	readonly kind: string | null;
	readonly title: string;
	readonly summary: string;
	/** Whether it brings a UI plugin of its own (which the shell loads instead). */
	readonly has_ui_plugin: boolean;
	readonly operations: readonly GeneratedOperation[];
	readonly settings: GeneratedForm | null;
	readonly accounts: GeneratedAccounts | null;
	/** The event types it publishes. */
	readonly event_types: readonly string[];
}

/** Everything the page generates from: udl's own operations, then each plugin's. */
export interface GeneratedUI {
	readonly plugins: readonly GeneratedPlugin[];
}

/** Whether `value` is an object the page can read fields from. */
function isObject(value: unknown): value is Record<string, unknown> {
	return typeof value === "object" && value !== null && !Array.isArray(value);
}

function isStringArray(value: unknown): value is readonly string[] {
	return Array.isArray(value) && value.every((one: unknown) => typeof one === "string");
}

function isView(value: unknown): value is View {
	return typeof value === "string" && (VIEWS as readonly string[]).includes(value);
}

function isHints(value: unknown): value is GeneratedHints {
	return (
		isObject(value) &&
		["read_only", "destructive", "idempotent", "open_world", "admin", "mcp"].every(
			(key) => typeof value[key] === "boolean",
		)
	);
}

function isUISpec(value: unknown): value is GeneratedUISpec {
	return (
		isObject(value) &&
		(value.group === null || typeof value.group === "string") &&
		typeof value.order === "number" &&
		isView(value.view) &&
		(value.icon === null || typeof value.icon === "string") &&
		typeof value.hidden === "boolean" &&
		(value.confirm_field === null || typeof value.confirm_field === "string")
	);
}

function isOperation(value: unknown): value is GeneratedOperation {
	return (
		isObject(value) &&
		typeof value.name === "string" &&
		typeof value.title === "string" &&
		typeof value.summary === "string" &&
		typeof value.description === "string" &&
		isHints(value.hints) &&
		isUISpec(value.ui) &&
		(value.method === null || typeof value.method === "string") &&
		(value.path === null || typeof value.path === "string")
	);
}

function isForm(value: unknown): value is GeneratedForm {
	return isObject(value) && isStringArray(value.read_only) && isStringArray(value.secret);
}

function isAccounts(value: unknown): value is GeneratedAccounts {
	return isObject(value) && isStringArray(value.connect) && isStringArray(value.access_levels);
}

function isPlugin(value: unknown): value is GeneratedPlugin {
	return (
		isObject(value) &&
		typeof value.name === "string" &&
		typeof value.title === "string" &&
		(value.kind === null || typeof value.kind === "string") &&
		Array.isArray(value.operations) &&
		value.operations.every(isOperation) &&
		(value.settings === null || isForm(value.settings)) &&
		(value.accounts === null || isAccounts(value.accounts)) &&
		isStringArray(value.event_types)
	);
}

/** What udl answered, when it is a generated UI (`null` when it is something else). */
export function generatedUI(value: unknown): GeneratedUI | null {
	if (!isObject(value) || !Array.isArray(value.plugins) || !value.plugins.every(isPlugin)) {
		return null;
	}
	return { plugins: value.plugins };
}

/** The plugin `name`'s page, or `undefined` when udl lists none of that name. */
export function pluginNamed(ui: GeneratedUI, name: string): GeneratedPlugin | undefined {
	return ui.plugins.find((plugin) => plugin.name === name);
}

/** The operations a page shows: not the hidden ones, in their groups. */
export function groupsOf(plugin: GeneratedPlugin): readonly OperationGroup[] {
	const shown = plugin.operations.filter((operation) => !operation.ui.hidden);
	const groups = new Map<string | null, GeneratedOperation[]>();
	for (const operation of shown) {
		const group = groups.get(operation.ui.group);
		if (group === undefined) {
			groups.set(operation.ui.group, [operation]);
		} else {
			group.push(operation);
		}
	}
	// (The ungrouped operations first — a plugin's own, before a group's — then the groups by
	// name; each in `order`, then by title, as the card says.)
	const ordered = [...groups.entries()].sort(([left], [right]) => {
		if (left === right) return 0;
		if (left === null) return -1;
		if (right === null) return 1;
		return left.localeCompare(right);
	});
	return ordered.map(([group, operations]) => ({
		group,
		operations: [...operations].sort(
			(left, right) => left.ui.order - right.ui.order || left.title.localeCompare(right.title),
		),
	}));
}

/** The operations of one heading (`null` for the ungrouped ones). */
export interface OperationGroup {
	readonly group: string | null;
	readonly operations: readonly GeneratedOperation[];
}

/** The tabs a plugin's page has, in the order the card lists them, each only when it has
 * something to show. */
export function tabsOf(plugin: GeneratedPlugin): readonly Tab[] {
	const tabs: Tab[] = [];
	if (plugin.operations.some((operation) => !operation.ui.hidden)) tabs.push("operations");
	if (plugin.settings !== null) tabs.push("settings");
	if (plugin.accounts !== null) tabs.push("accounts");
	if (plugin.event_types.length > 0) tabs.push("events");
	return tabs;
}

/** A tab of a plugin's page. */
export const TABS = ["operations", "settings", "accounts", "events"] as const;
export type Tab = (typeof TABS)[number];
