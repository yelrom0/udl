/**
 * --------
 * - index -
 * --------
 *
 * What a udl UI plugin is written against: `defineUIPlugin(spec)`, the `Mount` contract every
 * contribution follows, and the `UIContext` the shell gives it. The manifest's types (what a
 * bundle's `manifest.json` says) are `./manifest`'s, generated from udl's own model.
 *
 * Nothing here imports React: a plugin built with React takes `./react`'s `react(Component)`,
 * and a plugin built with anything else (or plain DOM) writes a `Mount` itself.
 */

import type { FieldWidgetProps } from "./schema";

export type {
	Contributions,
	Generated,
	GeneratedNav,
	NavItem,
	UIManifest,
	Widget,
} from "./manifest";
export type { FieldOption, FieldSpec, FieldWidget, FieldWidgetProps } from "./schema";
export {
	fieldsOf,
	fieldWidgets,
	isJsonObject,
	resolveRefs,
	valueAt,
	withoutValue,
	withValue,
} from "./schema";

/** The SDK's version: what a manifest's `sdk` range (`^MAJOR.MINOR`) is checked against. */
export const version = "1.1.0";

/** JSON, as the API sends it. */
export type JsonValue =
	| string
	| number
	| boolean
	| null
	| JsonValue[]
	| { [key: string]: JsonValue };

/** A JSON object (a request's body, an event's data). */
export type JsonObject = { [key: string]: JsonValue };

/** The plugin the contribution belongs to: its name, its version, and where its files are. */
export type PluginIdentity = {
	name: string;
	version: string;
	baseUrl: string;
};

/** The shell's theme: the mode, the daisyUI theme's name, and its tokens (CSS custom properties). */
export type Theme = {
	mode: "light" | "dark";
	name: string;
	tokens: Readonly<Record<string, string>>;
	/** Called on every change; returns what stops the listening. */
	onChange(listener: (theme: Theme) => void): () => void;
};

/** One of the shell's stores (read-only: its data is the shell's to change). */
export type Store<T = unknown> = {
	getState(): T;
	subscribe(listener: (state: T) => void): () => void;
};

/** The live connection: `subscribe` to the events a filter matches. */
export type EventSource = {
	subscribe(filter: string | JsonObject, callback: (event: JsonObject) => void): () => void;
};

/**
 * All a plugin gets from the shell. It holds no state of its own: the shell's stores, its
 * session, its theme and its router are what a plugin draws from and asks.
 */
export type UIContext = {
	/** The shell's typed API client (18.2), on the shell's own session. */
	api: unknown;
	/** Calls any operation by name: what an operation a plugin's plugin adds is reached by. */
	call(name: string, request?: JsonObject): Promise<JsonValue>;
	/** The shared live connection. */
	events: EventSource;
	theme: Theme;
	/** The shell's stores, by name (`runs`, `downloads`, `plugins`, `toasts`). */
	stores: Readonly<Record<string, Store>>;
	navigate(to: string): void;
	/** Put a live count on one of this plugin's own nav entries, or take it off with `null`
	 * (29.7's question count). `route` is the route as its manifest declares it. */
	setNavCount(route: string, count: number | null): void;
	toast(message: string, kind?: "info" | "success" | "warning" | "error"): void;
	confirm(question: string, options?: { ok?: string; cancel?: string }): Promise<boolean>;
	/** The plugin this contribution belongs to. */
	plugin: PluginIdentity;
};

/** What a mount hands back: the shell calls these as the props change and on navigation. */
export type Mounted<P> = {
	update(props: P): void;
	unmount(): void;
};

/**
 * What every contribution is, whatever it is built with: a function of the target, the props
 * and the context, which returns `update` and `unmount`.
 */
export type Mount<P> = (target: HTMLElement, props: P, ctx: UIContext) => Mounted<P>;

/** A page of the plugin's own (its manifest's `routes`). */
export type RouteProps = {
	/** The route (`inbox`, `message/$id`), as the shell has it. */
	path: string;
	/** What the route's `$name`s were. */
	params: Readonly<Record<string, string>>;
	/** The query's parameters. */
	search: Readonly<Record<string, string>>;
};

/** A settings panel for a plugin (its manifest's `settings`). */
export type SettingsProps = {
	/** The plugin whose settings these are. */
	plugin: string;
	/** What `get_plugin_settings` answers: the values, where each comes from, and their schema. */
	info: JsonObject;
	/** Saves these values (a secret's goes to the secret store, never to a database). The shell
	 * leaves out what must not be sent, as its own form does: a field `info.file_only` names
	 * (udl refuses it, and it is the config file's), and a value it did not change (a secret it
	 * gave as a mask). */
	save(values: JsonObject): Promise<void>;
};

/** How an integration's accounts are connected (its manifest's `accountSetup`). */
export type AccountSetupProps = {
	/** The integration. */
	integration: string;
	/** The account being set up, when one is being changed; a new one has none. */
	account?: string;
	/** Says it is done, and the shell reloads the accounts. */
	done(): void;
};

/** How one of the plugin's events looks, in the timeline and in detail (`eventViews`). */
export type EventViewProps = {
	/** The event: its type, its data, and when it happened. */
	event: JsonObject;
};

/** An editor for an integration's source filter (`filterEditors`). */
export type FilterEditorProps = {
	/** The filter, in its tree form (`null` for none). */
	value: JsonObject | null;
	onChange(value: JsonObject | null): void;
	/** The event types it may match (their names and their fields' schemas). */
	eventTypes: readonly JsonObject[];
};

/** An editor for an action kind's options (`actionEditors`). */
export type ActionEditorProps = {
	/** The options, as the action has them (they may hold templates). */
	options: JsonObject;
	onChange(options: JsonObject): void;
	/** The action kind's options model, as a JSON Schema. */
	schema: JsonObject;
};

/** Something in one of the shell's slots (`widgets`). */
export type WidgetProps = {
	/** The slot it is drawn in (`dashboard`...). */
	slot: string;
	/** The shell's plugin: the one whose widget this is. */
	plugin: PluginIdentity;
};

/** An operation, as a UI plugin that replaces part of its page is shown it (24.7). */
export type GuidedOperation = {
	/** Its name (the API's operationId). */
	name: string;
	title: string;
	summary: string;
	/** Where it opens: a list runs itself, a form waits for Run. */
	view: "list" | "detail" | "form" | "action";
	/** What it does to the world (udl's hints: read-only, destructive, admin...). */
	hints: Readonly<Record<string, boolean>>;
};

/** An operation's panel, replaced by a plugin's own (`operationViews`, 24.7). */
export type OperationViewProps = {
	/** The operation it stands for. */
	operation: GuidedOperation;
	/** Its request's JSON Schema (what a form would be drawn from). */
	schema: JsonValue;
	/** Runs it with these values, and answers what udl answered. */
	run(values: JsonObject): Promise<JsonValue>;
};

/** An operation's result, drawn by a plugin of its own (`resultViews`, 24.7). */
export type ResultViewProps = {
	/** The operation whose result this is. */
	operation: GuidedOperation;
	/** What it answered. */
	value: JsonValue;
};

/** What a UI plugin's entry exports: its contributions, by point. */
export type UIPluginSpec = {
	routes?: Record<string, Mount<RouteProps>>;
	settings?: Record<string, Mount<SettingsProps>>;
	accountSetup?: Record<string, Mount<AccountSetupProps>>;
	eventViews?: Record<string, Mount<EventViewProps>>;
	filterEditors?: Record<string, Mount<FilterEditorProps>>;
	actionEditors?: Record<string, Mount<ActionEditorProps>>;
	widgets?: Record<string, Mount<WidgetProps>>;
	/** The operations whose panel it replaces (its manifest's `operationViews`, 24.7). */
	operationViews?: Record<string, Mount<OperationViewProps>>;
	/** The operations whose result it draws (its manifest's `resultViews`, a `*` prefix too). */
	resultViews?: Record<string, Mount<ResultViewProps>>;
	/** Its field widgets, by the name a field's `x-udl-ui.widget` asks for (24.7). */
	fieldWidgets?: Record<string, Mount<FieldWidgetProps>>;
};

/**
 * A UI plugin's entry: `export default defineUIPlugin({ routes: { inbox: react(Inbox) } })`.
 * It is the spec itself (there is nothing to add), so a bundle built by hand is one too.
 */
export function defineUIPlugin<const S extends UIPluginSpec>(spec: S): S {
	return spec;
}
