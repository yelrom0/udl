import type { JsonObject, JsonValue } from "@udl/ui-sdk";

/** Whether `value` is a JSON object (not an array, not null). */
export function isObject(value: JsonValue | undefined): value is JsonObject {
	return typeof value === "object" && value !== null && !Array.isArray(value);
}

/** `value` as a JSON object, or an empty one when it is anything else. */
export function asObject(value: JsonValue | undefined): JsonObject {
	return isObject(value) ? value : {};
}

/** `value` as a list, or an empty one when it is anything else. */
export function asArray(value: JsonValue | undefined): JsonValue[] {
	return Array.isArray(value) ? value : [];
}

/** `value` as text, or `fallback` when it is anything else. */
export function asText(value: JsonValue | undefined, fallback = ""): string {
	return typeof value === "string" ? value : fallback;
}

/** `value` as a number, or `fallback` when it is anything else. */
export function asNumber(value: JsonValue | undefined, fallback = 0): number {
	return typeof value === "number" ? value : fallback;
}

/**
 * `value` as a number, when it is one: udl sends a `Decimal` as a string (`"0.42"`), and a page
 * that reads only numbers shows nothing of what it cost.
 */
export function asDecimal(value: JsonValue | undefined): number | null {
	if (typeof value === "number") {
		return Number.isFinite(value) ? value : null;
	}
	if (typeof value !== "string" || value.trim() === "") {
		return null;
	}
	const read = Number(value);
	return Number.isFinite(read) ? read : null;
}

/** `value` as a boolean: true only when it is true. */
export function asFlag(value: JsonValue | undefined): boolean {
	return value === true;
}

/** What an error says, in a line (what a store shows when a call failed). */
export function messageOf(error: unknown): string {
	return error instanceof Error ? error.message : String(error);
}
