/**
 * ------------
 * - result.ts -
 * ------------
 *
 * What a response is, as the generated UI shows it (24.5): a table, a description list, or JSON.
 *
 * - A list of objects is a table: `list_accounts`' `{accounts: [...]}` and `list_events`'
 *   `{events: [...]}` are an object whose one list is the rows, and a bare array is its own.
 * - A fan-out's answer (`{items: [{account, item}], failures, next_pages}`) is a table of its
 *   items, with the account each came from first: it has two lists, so it would be JSON.
 * - An object whose values are all scalars is a description list.
 * - Anything else — a scalar, a nested value, two lists (which one is the table?) — is JSON.
 *
 * The table's columns are the item's scalar fields, in `x-udl-ui.order`'s order then the
 * schema's: a field that is an object or a list is not a column, and one the schema does not
 * name is not shown (the JSON is there for that).
 */

import { type FieldSpec, fieldsOf, isJsonObject, type JsonValue, resolveRefs } from "@udl/ui-sdk";

/** A row of a table: one item of a list, as it came. */
export type Row = Readonly<Record<string, JsonValue>>;

/** An account a fan-out failed in, and why (11.4). */
export interface Failure {
	readonly account: string;
	/** The error's code (`rate_limited`), when it has one. */
	readonly code?: string;
	readonly message: string;
}

/** A table: what `list_accounts` and `list_events` are. */
export interface TableShape {
	readonly kind: "table";
	/** What the list is called (`Accounts`), when the schema says. */
	readonly caption?: string;
	readonly columns: readonly FieldSpec[];
	readonly rows: readonly Row[];
	/** The accounts a fan-out failed in: their items came back empty, not wrong. */
	readonly failures?: readonly Failure[];
}

/** A description list: a flat object, as `status` is. */
export interface FieldsShape {
	readonly kind: "fields";
	readonly fields: readonly FieldSpec[];
}

/** Anything else: JSON. */
export interface JsonShape {
	readonly kind: "json";
}

export type ResultShape = TableShape | FieldsShape | JsonShape;

/** Whether a field is a column: a scalar the table can show in one line. */
function isColumn(field: FieldSpec): boolean {
	return field.fields === undefined && field.widget !== "json";
}

/** The columns of a table of `items`. */
function columnsOf(items: JsonValue): readonly FieldSpec[] {
	return fieldsOf(items).filter(isColumn);
}

/** A row, when it is an object. */
function rowOf(value: JsonValue): Row | undefined {
	return isJsonObject(value) ? value : undefined;
}

/** Whether an array value is a list of objects (an empty one when the schema says its items are). */
function isRows(item: readonly JsonValue[], schema: JsonValue): boolean {
	const rows = item.map(rowOf);
	if (!rows.every((row) => row !== undefined)) {
		return false;
	}
	if (rows.length > 0) {
		return true;
	}
	// (An empty list is still a table when its schema says its items are objects: `{events: []}`.)
	const items = itemsOf(schema);
	return isJsonObject(items) && items.type === "object";
}

/** The one property of `value` whose value is a list of objects, when there is exactly one. */
function listOf(
	value: Readonly<Record<string, JsonValue>>,
	schema: JsonValue,
): readonly [string, readonly Row[]] | undefined {
	const found: [string, readonly Row[]][] = [];
	for (const [name, item] of Object.entries(value)) {
		if (!Array.isArray(item) || !isRows(item, propertyOf(schema, name))) {
			continue;
		}
		found.push([name, item as readonly Row[]]);
	}
	return found.length === 1 ? found[0] : undefined;
}

/** The schema's description of the property `name` of an object schema. */
function propertyOf(schema: JsonValue, name: string): JsonValue {
	if (!isJsonObject(schema)) {
		return {};
	}
	const properties = schema.properties;
	if (!isJsonObject(properties)) {
		return {};
	}
	return properties[name] ?? {};
}

/** The account column a fan-out's rows are tagged by (11.4). */
const ACCOUNT: FieldSpec = {
	name: "account",
	path: ["account"],
	label: "Account",
	widget: "text",
	required: true,
	advanced: false,
	order: -1,
	writeOnly: false,
};

/** Whether `entry` is one of a fan-out's items: an account, and what it found there. */
function isAccountItem(
	entry: Row,
): entry is Row & { readonly account: string; readonly item: Row } {
	return typeof entry.account === "string" && isJsonObject(entry.item);
}

/** Whether `schema` is a fan-out's answer: its `items` are `{account, item}` entries. */
function isFanOutSchema(schema: JsonValue): boolean {
	const entry = itemsOf(propertyOf(schema, "items"));
	const properties = isJsonObject(entry) ? entry.properties : undefined;
	return isJsonObject(properties) && "account" in properties && "item" in properties;
}

/** The accounts a fan-out failed in, as it says: its `failures`, each with an account and an error. */
function failuresOf(value: Readonly<Record<string, JsonValue>>): readonly Failure[] {
	const failures = value.failures;
	if (!Array.isArray(failures)) {
		return [];
	}
	const said: Failure[] = [];
	for (const failure of failures) {
		if (!isJsonObject(failure) || typeof failure.account !== "string") {
			continue;
		}
		const error = isJsonObject(failure.error) ? failure.error : {};
		const message = typeof error.message === "string" ? error.message : JSON.stringify(error);
		const code = typeof error.code === "string" ? { code: error.code } : {};
		said.push({ account: failure.account, ...code, message });
	}
	return said;
}

/**
 * A fan-out's answer (`FanOutResult[T]`: `{items: [{account, item}], failures, next_pages}`) as
 * a table: each item's own fields, with the account it came from first. A fan-out has two lists,
 * so it would otherwise be JSON — or a table of its "items" column alone, which says nothing.
 */
function fanOutOf(
	value: Readonly<Record<string, JsonValue>>,
	schema: JsonValue,
): TableShape | undefined {
	const items = value.items;
	if (!Array.isArray(items) || !isFanOutSchema(schema)) {
		return undefined;
	}
	const rows: Row[] = [];
	for (const entry of items) {
		if (!isJsonObject(entry) || !isAccountItem(entry)) {
			return undefined;
		}
		rows.push({ ...entry.item, account: entry.account });
	}
	// (An empty list, every account failed: the schema still says what the columns are.)
	const entry = itemsOf(propertyOf(schema, "items"));
	// (The item's own "account", when it has one, is the same account: one column, the tagging's.)
	const columns = [
		ACCOUNT,
		...columnsOf(propertyOf(entry, "item")).filter((column) => column.name !== ACCOUNT.name),
	];
	const failures = failuresOf(value);
	return { kind: "table", columns, rows, ...(failures.length > 0 ? { failures } : {}) };
}

/** The schema of a list's items (`{items: {...}}`), or nothing. */
function itemsOf(schema: JsonValue): JsonValue {
	return isJsonObject(schema) ? (schema.items ?? {}) : {};
}

/** What `value` is, as the UI shows it, given the response `schema` it came from. */
export function shapeOf(
	schema: JsonValue,
	value: JsonValue,
	document: JsonValue = schema,
): ResultShape {
	const said = resolveRefs(schema, document);

	if (Array.isArray(value)) {
		const rows = value.map(rowOf);
		if (rows.every((row) => row !== undefined)) {
			return { kind: "table", columns: columnsOf(itemsOf(said)), rows: rows as readonly Row[] };
		}
		return { kind: "json" };
	}

	if (!isJsonObject(value)) {
		return { kind: "json" };
	}

	const fanOut = fanOutOf(value, said);
	if (fanOut !== undefined) {
		return fanOut;
	}

	const list = listOf(value, said);
	if (list !== undefined) {
		const [name, rows] = list;
		const property = propertyOf(said, name);
		const caption = isJsonObject(property) ? property.title : undefined;
		return {
			kind: "table",
			...(typeof caption === "string" ? { caption } : {}),
			columns: columnsOf(itemsOf(property)),
			rows,
		};
	}

	// (A description list only for a flat object: a nested value is JSON, as it has no one line.)
	const nested = Object.values(value).some(
		(item) => item !== null && (typeof item === "object" || Array.isArray(item)),
	);
	if (nested) {
		return { kind: "json" };
	}
	const fields = fieldsOf(said).filter(isColumn);
	// (A schema that names none of its values has no description list to draw: the JSON is all.)
	return fields.length === 0 ? { kind: "json" } : { kind: "fields", fields };
}
