/**
 * ----------
 * - schema -
 * ----------
 *
 * A JSON Schema, as a form: `fieldsOf(schema)` is a pure function from the schema udl hands out
 * (an operation's request, a plugin's settings) to an ordered list of fields — its type, the
 * widget that shows it, whether it is required, its default, its choices, its own fields when it
 * is an object, and the `x-udl-ui` hints udl wrote (ui_field). An unknown shape is the `json`
 * widget: a form never fails to draw because a schema surprised it.
 *
 * It reads the schema; it never validates a value. The API does that, and its answer's message
 * says what was wrong.
 */

import type { JsonValue } from "./index";

/** How a form shows a field: what ui_field's `widget=` may say, and what a type means by default. */
export type FieldWidget =
	| "text"
	| "textarea"
	| "markdown"
	| "password"
	| "number"
	| "switch"
	| "select"
	| "multiselect"
	| "path"
	| "url"
	| "datetime"
	| "duration"
	| "json"
	| "account"
	| "code";

/** Every field widget there is (a hint that names another is ignored, as udl ignores one). */
export const fieldWidgets: readonly FieldWidget[] = [
	"text",
	"textarea",
	"markdown",
	"password",
	"number",
	"switch",
	"select",
	"multiselect",
	"path",
	"url",
	"datetime",
	"duration",
	"json",
	"account",
	"code",
];

/** One of a select's choices. */
export interface FieldOption {
	readonly value: JsonValue;
	/** What the choice says (its text, or its JSON). */
	readonly label: string;
}

/** A field of a form, as its schema says it. */
export interface FieldSpec {
	/** Its key in the value (the property's name). */
	readonly name: string;
	/** Where it is: `["options", "label"]` for a field of an object's. */
	readonly path: readonly string[];
	/** What it is called (`x-udl-ui.label`, the schema's title, or its name). */
	readonly label: string;
	/** What it is for (its description or help), when it says. */
	readonly help?: string;
	/** What to show in an empty box, when it says. */
	readonly placeholder?: string;
	/** The widget that shows it. */
	readonly widget: FieldWidget;
	/** Whether the value must have it. */
	readonly required: boolean;
	/** What it is when nothing is given. */
	readonly default?: JsonValue;
	/** Its choices, when it is a select. */
	readonly options?: readonly FieldOption[];
	/** Its own fields, when it is an object (the form shows them instead of one box). */
	readonly fields?: readonly FieldSpec[];
	/** What its items are, when it is a list (their widget, and their choices). */
	readonly items?: FieldSpec;
	/** Whether it sits in the form's advanced section. */
	readonly advanced: boolean;
	/** Where it sits (the lowest first): `x-udl-ui.order`, else the schema's own order. */
	readonly order: number;
	/** Whether it is a secret (write-only: never shown back). */
	readonly writeOnly: boolean;
}

type Schema = Readonly<Record<string, JsonValue>>;

/** Whether a value is a JSON object (not an array, not null). */
export function isJsonObject(value: unknown): value is Schema {
	return typeof value === "object" && value !== null && !Array.isArray(value);
}

function asObject(value: JsonValue | undefined): Schema | undefined {
	return isJsonObject(value) ? value : undefined;
}

function asArray(value: JsonValue | undefined): readonly JsonValue[] | undefined {
	return Array.isArray(value) ? value : undefined;
}

function asString(value: JsonValue | undefined): string | undefined {
	return typeof value === "string" ? value : undefined;
}

function isWidget(value: string): value is FieldWidget {
	return (fieldWidgets as readonly string[]).includes(value);
}

function asWidget(value: JsonValue | undefined): FieldWidget | undefined {
	const found = asString(value);
	return found !== undefined && isWidget(found) ? found : undefined;
}

/** What a choice says: its text, or the JSON of anything else. */
function labelOf(value: JsonValue): string {
	return typeof value === "string" ? value : JSON.stringify(value);
}

/**
 * A field's schema with the `anyOf`/`oneOf` pydantic writes for `T | None` taken off: the one
 * branch that is not `null`. Anything else (a real union) stays as it is — a box of JSON.
 */
function effective(property: Schema): Schema {
	for (const key of ["anyOf", "oneOf"] as const) {
		const branches = asArray(property[key]);
		if (branches === undefined || branches.length !== 2) {
			continue;
		}
		const [left, right] = branches;
		const isNull = (branch: JsonValue | undefined): boolean =>
			isJsonObject(branch) && (branch.type === "null" || branch.const === null);
		if (isNull(right) && isJsonObject(left)) {
			return left;
		}
		if (isNull(left) && isJsonObject(right)) {
			return right;
		}
	}
	return property;
}

/** The widget a field's type and format mean, when nothing says otherwise. */
function widgetOf(property: Schema): FieldWidget {
	if (property.writeOnly === true) {
		return "password";
	}
	switch (asString(property.format)) {
		case "date-time":
			return "datetime";
		case "uri":
		case "url":
			return "url";
		case "password":
			return "password";
		case "duration":
			return "duration";
	}
	if (asArray(property.enum) !== undefined) {
		return "select";
	}
	if (asString(property.type) === "array") {
		// (pydantic writes `list[Literal[...]]`'s enum under `items`: that is a multiselect.)
		return asArray(asObject(property.items)?.enum) === undefined ? "json" : "multiselect";
	}
	switch (asString(property.type)) {
		case "boolean":
			return "switch";
		case "integer":
		case "number":
			return "number";
		case "string":
			return "text";
		default:
			// (An object, an array, a union: a box of JSON, unless the schema says its own fields.)
			return "json";
	}
}

/** A field, from its property's schema. */
function fieldOf(
	name: string,
	path: readonly string[],
	property: Schema,
	index: number,
	required: boolean,
): FieldSpec {
	const hints = asObject(property["x-udl-ui"]) ?? {};
	// (What the schema says, with `T | None`'s anyOf taken off: a nullable field is its type's.)
	const said = effective(property);
	const own = fieldsOf(said, path);
	const item = asObject(said.items);
	const choices = asArray(said.enum) ?? asArray(item?.enum);
	const help = asString(hints.help) ?? asString(said.description);
	const placeholder = asString(hints.placeholder);
	return {
		name,
		path,
		label: asString(hints.label) ?? asString(said.title) ?? name,
		...(help === undefined ? {} : { help }),
		...(placeholder === undefined ? {} : { placeholder }),
		widget: asWidget(hints.widget) ?? widgetOf(said),
		required,
		...(said.default === undefined ? {} : { default: said.default }),
		...(choices === undefined
			? {}
			: { options: choices.map((value) => ({ value, label: labelOf(value) })) }),
		...(own.length === 0 ? {} : { fields: own }),
		...(item === undefined ? {} : { items: fieldOf(name, path, item, index, required) }),
		advanced: hints.advanced === true,
		order: typeof hints.order === "number" ? hints.order : index,
		writeOnly: said.writeOnly === true,
	};
}

/**
 * The fields of the form `schema` draws: its properties, in `x-udl-ui.order`'s order (then the
 * schema's own), each with what its type and hints say. A schema that is not an object with
 * properties has no fields (and no error).
 */
export function fieldsOf(schema: JsonValue, at: readonly string[] = []): readonly FieldSpec[] {
	const properties = asObject(asObject(schema)?.properties);
	if (properties === undefined) {
		return [];
	}
	const required = new Set((asArray(asObject(schema)?.required) ?? []).map((name) => String(name)));
	const found = Object.entries(properties).map(([name, property], index) =>
		fieldOf(name, [...at, name], asObject(property) ?? {}, index, required.has(name)),
	);
	// (A stable sort: a field without an order keeps its declared place, and one with an order
	// sits at it — two fields of the same order keep the schema's.)
	return found.sort((left, right) => left.order - right.order);
}

/** The thing a JSON Pointer (`#/components/schemas/X`) points at in `root`, when it is there. */
function pointedAt(root: JsonValue, pointer: string): JsonValue | undefined {
	let found: JsonValue | undefined = root;
	for (const part of pointer.replace(/^#\//, "").split("/")) {
		if (!isJsonObject(found)) {
			return undefined;
		}
		found = found[part.replaceAll("~1", "/").replaceAll("~0", "~")];
	}
	return found;
}

/**
 * `schema` with its `$ref`s into `root` (an OpenAPI document's `#/components/schemas/X`, or its
 * `#/$defs/X`) replaced by what they point at, so a form can read it: a reference it cannot
 * follow — or one that refers to itself, a filter tree — is an empty schema, which draws a box of
 * JSON rather than a page that never ends.
 */
export function resolveRefs(schema: JsonValue, root: JsonValue = schema): JsonValue {
	const resolve = (node: JsonValue, seen: readonly string[]): JsonValue => {
		if (Array.isArray(node)) {
			return node.map((item) => resolve(item, seen));
		}
		if (!isJsonObject(node)) {
			return node;
		}
		const reference = node.$ref;
		if (typeof reference === "string" && reference.startsWith("#/")) {
			// (A field's own keywords sit next to its `$ref` — its description, default and
			// `x-udl-ui` — and they are the field's: they win over the definition's.)
			const siblings = Object.fromEntries(
				Object.entries(node)
					.filter(([name]) => name !== "$ref")
					.map(([name, value]) => [name, resolve(value, seen)]),
			);
			const target = pointedAt(root, reference);
			if (target === undefined || seen.includes(reference)) {
				return siblings;
			}
			const found = resolve(target, [...seen, reference]);
			return isJsonObject(found) ? { ...found, ...siblings } : found;
		}
		return Object.fromEntries(
			Object.entries(node)
				.filter(([name]) => name !== "$ref")
				.map(([name, value]) => [name, resolve(value, seen)]),
		);
	};
	return resolve(schema, []);
}

/** The value at `path` in `value`, when it is there. */
export function valueAt(value: JsonValue, path: readonly string[]): JsonValue | undefined {
	let found: JsonValue | undefined = value;
	for (const name of path) {
		const object = asObject(found);
		if (object === undefined) {
			return undefined;
		}
		found = object[name];
	}
	return found;
}

/** `value` with `path` set to `set` (the objects on the way made where they are not there). */
export function withValue(value: JsonValue, path: readonly string[], set: JsonValue): JsonValue {
	const [name, ...rest] = path;
	if (name === undefined) {
		return set;
	}
	const object = asObject(value) ?? {};
	const current = object[name];
	const next = rest.length === 0 ? set : withValue(current ?? {}, rest, set);
	return { ...object, [name]: next };
}

/** `value` with `path` taken out (a cleared field is not sent as null). */
export function withoutValue(value: JsonValue, path: readonly string[]): JsonValue {
	const [name, ...rest] = path;
	if (name === undefined) {
		return value;
	}
	const object = asObject(value);
	if (object === undefined || !(name in object)) {
		return value;
	}
	if (rest.length === 0) {
		const { [name]: _removed, ...others } = object;
		return others;
	}
	return { ...object, [name]: withoutValue(object[name] ?? {}, rest) };
}
