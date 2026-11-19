/**
 * ----------------
 * - ResultView.tsx -
 * ----------------
 *
 * A response, shown the way the generated UI shows it (24.5): `list_accounts`' and `list_events`'
 * responses as tables — sortable by a column, paged — a flat object as a description list, and
 * anything else as JSON (`JsonTree`). What shape a response is comes from `shapeOf` (`./result`),
 * which reads the response's schema.
 *
 * A value is drawn by what the schema says it is: a URL is a link, a datetime is `TimeAgo`, a
 * boolean is a badge. A string from udl is always a text node — React escapes it, so nothing a
 * plugin or a page sent can run here.
 */

import type { FieldSpec, JsonValue } from "@udl/ui-sdk";
import { useMemo, useState } from "react";
import { JsonTree } from "../components/JsonTree";
import { Paged } from "../components/Paged";
import { TimeAgo } from "../components/TimeAgo";
import { type Failure, type ResultShape, type Row, shapeOf } from "./result";

/** How many rows a page holds. */
export const PAGE_SIZE = 25;

export interface ResultViewProps {
	/** The response's schema, from the operation's `openapi.json` entry. */
	readonly schema: JsonValue;
	/** What the operation answered. */
	readonly value: JsonValue;
	/** The document the schema's `$ref`s point into (the API's OpenAPI one), when it has them. */
	readonly document?: JsonValue;
	/** How many rows a page holds (25 by default). */
	readonly pageSize?: number;
}

export function ResultView({
	schema,
	value,
	document = schema,
	pageSize = PAGE_SIZE,
}: ResultViewProps) {
	const shape = useMemo(() => shapeOf(schema, value, document), [schema, value, document]);

	if (shape.kind === "table") {
		return <Table shape={shape} pageSize={pageSize} />;
	}
	if (shape.kind === "fields") {
		return <DescriptionList fields={shape.fields} value={value} />;
	}
	return <JsonTree value={value} />;
}

interface TableProps {
	readonly shape: Extract<ResultShape, { kind: "table" }>;
	readonly pageSize: number;
}

interface Sort {
	readonly column: string;
	/** From smallest to largest (false: the other way). */
	readonly ascending: boolean;
}

/** Whether a cell holds nothing. */
function blank(value: JsonValue | undefined): boolean {
	return value === undefined || value === null;
}

/** A row with the key it keeps: the row's own JSON, and where it sits in the answer. */
interface KeyedRow {
	readonly row: Row;
	readonly key: string;
}

/** The rows in the sort's order: numbers as numbers, nothing last, the rest as text. */
function sorted<T extends KeyedRow>(rows: readonly T[], sort: Sort | null): readonly T[] {
	if (sort === null) {
		return rows;
	}
	const direction = sort.ascending ? 1 : -1;
	return [...rows].sort((left, right) => {
		const a = left.row[sort.column];
		const b = right.row[sort.column];
		if (a === b) {
			return 0;
		}
		// (Nothing is last, whichever way it is sorted: a blank cell is not a small one, and
		// `undefined` and `null` are the same nothing — else the two orders disagree.)
		if (blank(a) && blank(b)) {
			return 0;
		}
		if (blank(a)) {
			return 1;
		}
		if (blank(b)) {
			return -1;
		}
		if (typeof a === "number" && typeof b === "number") {
			return (a - b) * direction;
		}
		if (typeof a === "boolean" && typeof b === "boolean") {
			return (Number(a) - Number(b)) * direction;
		}
		return String(a).localeCompare(String(b)) * direction;
	});
}

function Table({ shape, pageSize }: TableProps) {
	const [sort, setSort] = useState<Sort | null>(null);
	const [page, setPage] = useState(1);
	// (The key is the row's place in the answer, before any sorting: sorting moves a row with
	// its key, so it is moved and not drawn again, and two rows that are the same keep two.)
	const keyed = useMemo(
		() => shape.rows.map((row, index) => ({ row, key: `${JSON.stringify(row)}#${index}` })),
		[shape.rows],
	);
	const rows = useMemo(() => sorted(keyed, sort), [keyed, sort]);
	const pages = Math.max(1, Math.ceil(rows.length / pageSize));
	// (A smaller answer than the page that was open: the last page there is, never an empty one.)
	const current = Math.min(page, pages);
	const shown = rows.slice((current - 1) * pageSize, current * pageSize);
	const failures = shape.failures ?? [];

	const change = (column: string): void => {
		setPage(1);
		setSort((was) =>
			was === null || was.column !== column
				? { column, ascending: true }
				: { column, ascending: !was.ascending },
		);
	};

	if (shape.rows.length === 0) {
		return (
			<div>
				<p className="py-6 text-center text-sm opacity-60">Nothing yet.</p>
				<Failures failures={failures} />
			</div>
		);
	}

	return (
		<div className="overflow-x-auto">
			{shape.caption === undefined ? null : (
				<h3 className="mb-2 text-sm font-medium">{shape.caption}</h3>
			)}
			<table className="table table-zebra table-sm">
				<thead>
					<tr>
						{shape.columns.map((column) => {
							const by = sort?.column === column.name ? (sort.ascending ? " ↑" : " ↓") : "";
							return (
								<th key={column.name} scope="col">
									<button
										type="button"
										className="link link-hover"
										onClick={() => change(column.name)}
									>
										{column.label}
										{by}
									</button>
								</th>
							);
						})}
					</tr>
				</thead>
				<tbody>
					{shown.map(({ row, key }) => (
						<tr key={key}>
							{shape.columns.map((column) => (
								<td key={column.name}>
									<Value field={column} value={row[column.name]} />
								</td>
							))}
						</tr>
					))}
				</tbody>
			</table>
			{pages > 1 ? <Paged page={current} pages={pages} onChange={setPage} /> : null}
			<Failures failures={failures} />
		</div>
	);
}

/** The accounts a fan-out failed in: their items are empty, and this is why. */
function Failures({ failures }: { readonly failures: readonly Failure[] }) {
	if (failures.length === 0) {
		return null;
	}
	return (
		<ul className="mt-2 space-y-1 text-sm">
			{failures.map((failure, index) => (
				<li key={`${failure.account}#${index}`} className="text-error">
					{failure.account}: {failure.code === undefined ? "" : `${failure.code}: `}
					{failure.message}
				</li>
			))}
		</ul>
	);
}

/** Whether a string is a link a browser may follow: `http` or `https`, nothing else. */
function isHttp(value: string): boolean {
	const scheme = /^(https?):\/\//i.exec(value);
	return scheme !== null;
}

interface ValueProps {
	readonly field: FieldSpec;
	readonly value: JsonValue | undefined;
}

/** A value, drawn by what the schema says it is. */
export function Value({ field, value }: ValueProps) {
	if (value === undefined || value === null) {
		return <span className="opacity-40">—</span>;
	}
	if (typeof value === "object") {
		// (A value that is not what the schema said: the JSON is the honest thing to show.)
		return <JsonTree value={value} />;
	}
	if (field.widget === "switch") {
		return (
			<span className={`badge ${value === true ? "badge-success" : "badge-ghost"}`}>
				{value === true ? "yes" : "no"}
			</span>
		);
	}
	if (field.widget === "url" && typeof value === "string" && isHttp(value)) {
		return (
			<a href={value} target="_blank" rel="noreferrer" className="link link-primary">
				{value}
			</a>
		);
	}
	if (field.widget === "datetime" && typeof value === "string") {
		return <TimeAgo at={value} />;
	}
	if (field.widget === "number") {
		return <span className="tabular-nums">{String(value)}</span>;
	}
	return <span>{String(value)}</span>;
}

interface DescriptionListProps {
	readonly fields: readonly FieldSpec[];
	readonly value: JsonValue;
}

function DescriptionList({ fields, value }: DescriptionListProps) {
	const record: Readonly<Record<string, JsonValue>> =
		typeof value === "object" && value !== null && !Array.isArray(value) ? value : {};
	return (
		<dl className="grid grid-cols-[max-content_1fr] gap-x-6 gap-y-2 text-sm">
			{fields.map((field) => (
				<div key={field.name} className="contents">
					<dt className="font-medium">{field.label}</dt>
					<dd>
						<Value field={field} value={record[field.name]} />
					</dd>
				</div>
			))}
		</dl>
	);
}
