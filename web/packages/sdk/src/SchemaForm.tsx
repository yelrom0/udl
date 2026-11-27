/**
 * ----------------
 * - SchemaForm.tsx -
 * ----------------
 *
 * A form drawn from a JSON Schema — an operation's request, a plugin's settings — the way udl's
 * generated UI shows them (24.4): one control per field, in the schema's order (the advanced
 * ones in a section of their own), a nested object's own fields under it, and a field's error
 * said at the field. It is controlled: it draws `value` and calls `onChange` with what the user
 * typed. What a value means is the API's to say; a box of JSON that does not hold JSON yet stays
 * in the box, and the value is not changed.
 *
 * The fields come from `fieldsOf` (`./schema`): a field with a widget udl has is that widget,
 * and anything else is a box of JSON — never an error. It is exported from this package's
 * `react` entry, so a UI plugin draws the same form udl's own pages do.
 */

import { type ReactNode, useId, useState } from "react";
import type { JsonValue } from "./index";
import {
	type FieldSpec,
	type FieldWidgetProps,
	fieldsOf,
	isJsonObject,
	resolveRefs,
	valueAt,
	withoutValue,
	withValue,
} from "./schema";

/** A form's value: a JSON object, as a request's body or a plugin's settings. */
export type FormValue = Readonly<Record<string, JsonValue>>;

export type { FieldWidgetProps } from "./schema";

export interface SchemaFormProps {
	readonly schema: JsonValue;
	/** The document the schema's `$ref`s point into (the API's OpenAPI one), when it has them. */
	readonly document?: JsonValue;
	/** What it is now (what the form draws). */
	readonly value: FormValue;
	/** What the user changed it to. */
	readonly onChange: (value: FormValue) => void;
	/** What the API said was wrong, by the field's path (`options.label`), when it said. */
	readonly errors?: Readonly<Record<string, string>>;
	/** The fields only the config file sets: shown, and not editable. */
	readonly readOnly?: readonly string[];
	/** The fields that are secrets: a password box, and their value is never drawn back. */
	readonly secret?: readonly string[];
	/** A plugin's own widgets, by the widget's name (`x-udl-ui.widget`). */
	readonly widgets?: Readonly<Record<string, (props: FieldWidgetProps) => ReactNode>>;
	/** Whether nothing may be changed (a read-only operation, or one that is running). */
	readonly disabled?: boolean;
}

/** The path of a field, as an error is keyed by it (`options.label`). */
function keyOf(field: FieldSpec): string {
	return field.path.join(".");
}

export function SchemaForm({
	schema,
	document = schema,
	value,
	onChange,
	errors = {},
	readOnly = [],
	secret = [],
	widgets = {},
	disabled = false,
}: SchemaFormProps) {
	// (A page may draw two forms with a field of the same name — a plugin's operations page draws
	// one per operation — so each form's field ids are its own.)
	const prefix = useId();
	const drawn = resolveRefs(schema, document);
	const fields = fieldsOf(drawn);
	const plain = fields.filter((field) => !field.advanced);
	const advanced = fields.filter((field) => field.advanced);
	// (What it is for: the schema's title, which is what a screen reader announces the form as.)
	const title = isJsonObject(drawn) ? drawn.title : undefined;

	return (
		<form
			className="flex flex-col gap-4"
			aria-label={typeof title === "string" ? title : undefined}
			onSubmit={(event) => {
				// (A form's submit is the page's: the operation's Run button is its own.)
				event.preventDefault();
			}}
		>
			{plain.map((field) => (
				<Field
					key={field.name}
					field={field}
					prefix={prefix}
					value={value}
					onChange={onChange}
					errors={errors}
					readOnly={readOnly}
					secret={secret}
					widgets={widgets}
					disabled={disabled}
				/>
			))}
			{advanced.length === 0 ? null : (
				<details className="collapse-arrow collapse rounded-box bg-base-200">
					<summary className="collapse-title text-sm font-medium">Advanced</summary>
					<div className="collapse-content flex flex-col gap-4">
						{advanced.map((field) => (
							<Field
								key={field.name}
								field={field}
								prefix={prefix}
								value={value}
								onChange={onChange}
								errors={errors}
								readOnly={readOnly}
								secret={secret}
								widgets={widgets}
								disabled={disabled}
							/>
						))}
					</div>
				</details>
			)}
		</form>
	);
}

interface FieldProps extends Omit<SchemaFormProps, "schema" | "document" | "value" | "onChange"> {
	readonly field: FieldSpec;
	/** What makes this form's field ids its own: two forms on a page may name a field alike. */
	readonly prefix: string;
	readonly value: FormValue;
	readonly onChange: (value: FormValue) => void;
}

function Field({
	field,
	prefix,
	value,
	onChange,
	errors = {},
	readOnly = [],
	secret = [],
	widgets = {},
	disabled = false,
}: FieldProps) {
	const key = keyOf(field);
	const error = errors[key];
	const fileOnly = readOnly.includes(key) || readOnly.includes(field.name);
	const isSecret = secret.includes(key) || secret.includes(field.name) || field.writeOnly;
	const locked = disabled || fileOnly;
	const current = valueAt(value, field.path);
	const set = (found: JsonValue): void => {
		const next = withValue(value, field.path, found);
		if (isJsonObject(next)) {
			onChange(next);
		}
	};
	// (A field cleared is not sent at all: `null` for a non-nullable field with a default is a 422.)
	const clear = (): void => {
		const next = withoutValue(value, field.path);
		if (isJsonObject(next)) {
			onChange(next);
		}
	};

	if (field.fields !== undefined && field.fields.length > 0) {
		// (An object's own fields: they are the form's, one level in.)
		return (
			<fieldset className="rounded-box border border-base-300 p-3">
				<legend className="px-1 text-sm font-medium">{field.label}</legend>
				{field.help === undefined ? null : <p className="mb-2 text-xs opacity-60">{field.help}</p>}
				<div className="flex flex-col gap-3">
					{field.fields.map((inner) => (
						<Field
							key={inner.name}
							field={inner}
							prefix={prefix}
							value={value}
							onChange={onChange}
							errors={errors}
							readOnly={readOnly}
							secret={secret}
							widgets={widgets}
							disabled={disabled}
						/>
					))}
				</div>
			</fieldset>
		);
	}

	// (A widget the field asks for by name wins: a plugin's own, for a field udl has no control
	// for. What it does not have falls back to the widget its type means.)
	const own =
		(field.asked === undefined ? undefined : widgets[field.asked]) ?? widgets[field.widget];
	const control =
		own === undefined ? (
			<Control
				id={`${prefix}-${key}`}
				field={field}
				value={current}
				onChange={set}
				onClear={clear}
				disabled={locked}
				invalid={error !== undefined}
				secret={isSecret}
			/>
		) : (
			own({ field, value: current, onChange: set, disabled: locked, invalid: error !== undefined })
		);

	return (
		<div className="form-control" data-field={key}>
			<label className="label" htmlFor={`${prefix}-${key}`}>
				<span className="label-text font-medium">
					{field.label}
					{field.required ? (
						<span className="text-error" aria-hidden="true">
							{" "}
							*
						</span>
					) : null}
				</span>
				{fileOnly ? (
					<span className="label-text-alt opacity-60">set in the config file</span>
				) : null}
			</label>
			{control}
			{field.help === undefined ? null : <p className="mt-1 text-xs opacity-60">{field.help}</p>}
			{error === undefined ? null : (
				<p className="mt-1 text-xs text-error" role="alert">
					{error}
				</p>
			)}
		</div>
	);
}

interface ControlProps {
	/** The field's element id, this form's own (see `Field`'s `prefix`). */
	readonly id: string;
	readonly field: FieldSpec;
	readonly value: JsonValue | undefined;
	readonly onChange: (value: JsonValue) => void;
	/** The field was cleared: it is taken out of the value, not set to null. */
	readonly onClear: () => void;
	readonly disabled: boolean;
	readonly invalid: boolean;
	readonly secret: boolean;
}

function Control({ id, field, value, onChange, onClear, disabled, invalid, secret }: ControlProps) {
	const className = `input w-full${invalid ? " input-error" : ""}`;
	const text = typeof value === "string" || typeof value === "number" ? String(value) : "";

	if (secret || field.widget === "password") {
		return (
			<SecretBox
				id={id}
				className={className}
				set={value !== undefined}
				{...(field.placeholder === undefined ? {} : { placeholder: field.placeholder })}
				disabled={disabled}
				onChange={onChange}
				onClear={onClear}
			/>
		);
	}
	if (field.widget === "switch") {
		return (
			<input
				id={id}
				type="checkbox"
				className="toggle"
				checked={value === true}
				disabled={disabled}
				onChange={(event) => onChange(event.target.checked)}
			/>
		);
	}
	if (field.widget === "select") {
		const choices = field.options ?? [];
		const chosen = choices.find((option) => option.value === value);
		return (
			<select
				id={id}
				className={`select w-full${invalid ? " select-error" : ""}`}
				value={chosen === undefined ? "" : chosen.label}
				disabled={disabled}
				onChange={(event) => {
					const picked = choices.find((option) => option.label === event.target.value);
					if (picked === undefined) {
						onClear();
					} else {
						onChange(picked.value);
					}
				}}
			>
				<option value="">{field.required ? "(choose)" : "(none)"}</option>
				{choices.map((option) => (
					<option key={option.label} value={option.label}>
						{option.label}
					</option>
				))}
			</select>
		);
	}
	if (field.widget === "multiselect") {
		return (
			<MultiSelect field={field} value={value} onChange={onChange} disabled={disabled} id={id} />
		);
	}
	if (field.widget === "number") {
		return (
			<input
				id={id}
				type="number"
				className={className}
				value={text}
				disabled={disabled}
				onChange={(event) => {
					const typed = event.target.value;
					if (typed === "") {
						onClear();
					} else {
						onChange(Number(typed));
					}
				}}
			/>
		);
	}
	if (field.widget === "textarea" || field.widget === "markdown") {
		return (
			<textarea
				id={id}
				className={`textarea w-full${invalid ? " textarea-error" : ""}`}
				value={text}
				disabled={disabled}
				placeholder={field.placeholder ?? ""}
				onChange={(event) => onChange(event.target.value)}
			/>
		);
	}
	if (field.widget === "lines") {
		return (
			<LinesBox
				id={id}
				value={value}
				{...(field.placeholder === undefined ? {} : { placeholder: field.placeholder })}
				disabled={disabled}
				invalid={invalid}
				onChange={onChange}
				onClear={onClear}
			/>
		);
	}
	if (field.widget === "json") {
		return (
			<JsonBox id={id} value={value} onChange={onChange} onClear={onClear} disabled={disabled} />
		);
	}
	return (
		<input
			id={id}
			type="text"
			className={className}
			value={text}
			disabled={disabled}
			placeholder={field.placeholder ?? ""}
			onChange={(event) => onChange(event.target.value)}
		/>
	);
}

interface SecretBoxProps {
	readonly id: string;
	readonly className: string;
	/** Whether the form holds a value for it (it is not drawn back, only said to be set). */
	readonly set: boolean;
	/** What goes in it, when the field says (`x-udl-ui`'s placeholder). */
	readonly placeholder?: string;
	readonly disabled: boolean;
	readonly onChange: (value: JsonValue) => void;
	readonly onClear: () => void;
}

/**
 * A password box that is never drawn back: it holds what was typed into it now, and never the
 * value the form holds — a secret that is set says so, and is not in the page's DOM.
 */
function SecretBox({
	id,
	className,
	set,
	placeholder,
	disabled,
	onChange,
	onClear,
}: SecretBoxProps) {
	const [typed, setTyped] = useState("");
	return (
		<input
			id={id}
			type="password"
			className={className}
			value={typed}
			disabled={disabled}
			// (What the field says goes in it while it is empty: a token that may be an `op://`
			// reference is not "(not set)". A value that is there still says so — it is never
			// drawn back, so "(set)" is the only way to know.)
			placeholder={set ? "(set: type to replace)" : (placeholder ?? "(not set)")}
			onChange={(event) => {
				setTyped(event.target.value);
				if (event.target.value === "") {
					// (Erased: the field is taken out, not saved over the stored secret as "".)
					onClear();
				} else {
					onChange(event.target.value);
				}
			}}
		/>
	);
}

interface LinesBoxProps {
	readonly id: string;
	readonly value: JsonValue | undefined;
	/** What goes on a line, when the field says (`x-udl-ui`'s placeholder). */
	readonly placeholder?: string;
	readonly disabled: boolean;
	readonly invalid: boolean;
	readonly onChange: (value: JsonValue) => void;
	readonly onClear: () => void;
}

/**
 * A list of lines (29.9): one item a line, for the lists that are typed as lines rather than as
 * a JSON array (`repos`, the plan files). What was typed is kept as it was, so a line that is
 * half-typed is not turned into an item and then read back trimmed; the value is the lines that
 * are not empty, each trimmed.
 */
function LinesBox({ id, value, placeholder, disabled, invalid, onChange, onClear }: LinesBoxProps) {
	const printed = Array.isArray(value) ? value.map((item) => String(item)).join("\n") : "";
	const [text, setText] = useState(printed);
	const [was, setWas] = useState(printed);

	// (The value changed from outside the box — a reset, a loaded one: draw it again. What was
	// typed is what set it, so `was` follows the lines the box sent.)
	if (printed !== was) {
		setWas(printed);
		setText(printed);
	}

	return (
		<textarea
			id={id}
			className={`textarea w-full${invalid ? " textarea-error" : ""}`}
			value={text}
			disabled={disabled}
			{...(placeholder === undefined ? {} : { placeholder })}
			onChange={(event) => {
				const typed = event.target.value;
				setText(typed);
				const lines = typed
					.split("\n")
					.map((line) => line.trim())
					.filter((line) => line !== "");
				setWas(lines.join("\n"));
				if (lines.length === 0) {
					// (Emptied: the field is taken out, not sent as `[]`, which a `min_length=1` list refuses.)
					onClear();
					return;
				}
				onChange(lines);
			}}
		/>
	);
}

interface JsonBoxProps {
	readonly id: string;
	readonly value: JsonValue | undefined;
	readonly onChange: (value: JsonValue) => void;
	readonly onClear: () => void;
	readonly disabled: boolean;
}

/**
 * A box of JSON: what it holds, printed. What is typed is kept as text while it is not JSON
 * (half-typed), and the value is only changed when it is — so a form never sends half a value.
 */
function JsonBox({ id, value, onChange, onClear, disabled }: JsonBoxProps) {
	const printed = value === undefined ? "" : JSON.stringify(value, null, 2);
	const [text, setText] = useState(printed);
	const [was, setWas] = useState(printed);

	// (The value changed from outside the box — a reset, a loaded account: draw it again.)
	if (printed !== was) {
		setWas(printed);
		setText(printed);
	}

	return (
		<textarea
			id={id}
			className="textarea w-full font-mono text-xs"
			value={text}
			disabled={disabled}
			rows={Math.min(12, Math.max(3, text.split("\n").length))}
			onChange={(event) => {
				setText(event.target.value);
				if (event.target.value.trim() === "") {
					// (Emptied: the field is taken out, not sent as null, which a non-nullable one refuses.)
					onClear();
					return;
				}
				try {
					onChange(JSON.parse(event.target.value) as JsonValue);
				} catch {
					// (Not JSON yet: it stays in the box, and the value does not change.)
				}
			}}
		/>
	);
}

interface MultiSelectProps {
	readonly id: string;
	readonly field: FieldSpec;
	readonly value: JsonValue | undefined;
	readonly onChange: (value: JsonValue) => void;
	readonly disabled: boolean;
}

/** A set of choices: the value is the list of the ones chosen. */
function MultiSelect({ id, field, value, onChange, disabled }: MultiSelectProps) {
	const chosen = new Set(
		Array.isArray(value) ? value.map((one) => (typeof one === "string" ? one : String(one))) : [],
	);
	return (
		<div id={id} className="flex flex-wrap gap-3">
			{(field.options ?? []).map((option) => {
				const name = typeof option.value === "string" ? option.value : String(option.value);
				return (
					<label key={name} className="label cursor-pointer gap-2">
						<input
							type="checkbox"
							className="checkbox"
							checked={chosen.has(name)}
							disabled={disabled}
							onChange={(event) => {
								const next = new Set(chosen);
								if (event.target.checked) {
									next.add(name);
								} else {
									next.delete(name);
								}
								onChange([...next]);
							}}
						/>
						<span className="label-text">{option.label}</span>
					</label>
				);
			})}
		</div>
	);
}
