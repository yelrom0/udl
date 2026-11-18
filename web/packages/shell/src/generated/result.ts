/**
 * ------------
 * - result.ts -
 * ------------
 *
 * What a response is, as the generated UI shows it (24.5): a table, a description list, or JSON.
 *
 * - A list of objects is a table: `list_accounts`' `{accounts: [...]}` and `list_events`'
 *   `{events: [...]}` are an object whose one list is the rows, and a bare array is its own.
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

/** A table: what `list_accounts` and `list_events` are. */
export interface TableShape {
	readonly kind: "table";
	/** What the list is called (`Accounts`), when the schema says. */
	readonly caption?: string;
	readonly columns: readonly FieldSpec[];
	readonly rows: readonly Row[];
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
