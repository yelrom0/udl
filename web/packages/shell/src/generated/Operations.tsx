/**
 * ----------------
 * - Operations.tsx -
 * ----------------
 *
 * A plugin's operations, as its page draws them (24.6): in their groups, a list's table run on
 * open with a refresh, a detail's or a form's fields with a Run, an action's button — and the
 * result under each, in 24.5's `<ResultView>`. What changes something is asked first; what udl
 * refuses says why, its hint included.
 *
 * Since 24.7 the plugin's own UI plugin may draw any of it instead: an operation's whole panel
 * (`operationViews`), only its result (`resultViews`), or a field of the form (`fieldWidgets`).
 */

import type { UdlApi } from "@udl/api";
import { fieldsOf, type JsonObject, type JsonValue } from "@udl/ui-sdk";
import { useCallback, useEffect, useRef, useState } from "react";
import type { Registry } from "../plugins/registry";
import { contributed, shellRegistry } from "../plugins/shell";
import { PluginSlot } from "../plugins/slot";
import { ask } from "../stores/confirms";
import { confirmation } from "./confirm";
import { ResultView } from "./ResultView";
import { jsonValue, runOperation, saidBy } from "./run";
import { SchemaForm } from "./SchemaForm";
import {
	type GeneratedHints,
	type GeneratedOperation,
	type GeneratedPlugin,
	groupsOf,
} from "./spec";
import { useFieldWidgets } from "./widgets";

/** What running an operation ended in: its answer, or what udl said. */
type Answer =
	| { readonly value: JsonValue }
	| { readonly problem: { readonly message: string; readonly hint: string | null } };

export interface OperationsTabProps {
	readonly plugin: GeneratedPlugin;
	readonly api: UdlApi;
	/** The order the page's guidance asks the groups to be in (24.7). */
	readonly groupOrder?: readonly string[];
	/** Where the contributions are (a test's own; the shell's when none is given). */
	readonly registry?: Registry;
}

/** The operations, in their groups (the ungrouped ones first). */
export function OperationsTab({
	plugin,
	api,
	groupOrder = [],
	registry = shellRegistry,
}: OperationsTabProps) {
	const groups = groupsOf(plugin, groupOrder);
	return (
		<div className="flex flex-col gap-6">
			{groups.map((group) => (
				<section key={group.group ?? ""}>
					{group.group !== null && <h3 className="mb-2 text-lg font-semibold">{group.group}</h3>}
					<div className="flex flex-col gap-4">
						{group.operations.map((operation) => (
							<OperationCard
								key={operation.name}
								operation={operation}
								api={api}
								registry={registry}
							/>
						))}
					</div>
				</section>
			))}
		</div>
	);
}

/** One operation, as its UI plugin is shown it (24.7's `operation` and `result` props). */
function guidedOf(operation: GeneratedOperation) {
	return {
		name: operation.name,
		title: operation.title,
		summary: operation.summary,
		view: operation.ui.view,
		hints: hintsOf(operation.hints),
	};
}

/** The hints as a record a plugin reads by name (`read_only`, `destructive`...). */
function hintsOf(hints: GeneratedHints): Readonly<Record<string, boolean>> {
	return { ...hints };
}

/** One operation: what it is, its fields (when it has any), and what it answered. */
export function OperationCard({
	operation,
	api,
	registry = shellRegistry,
}: {
	readonly operation: GeneratedOperation;
	readonly api: UdlApi;
	readonly registry?: Registry;
}) {
	const [values, setValues] = useState<JsonObject>({});
	const [answer, setAnswer] = useState<Answer | null>(null);
	const [busy, setBusy] = useState(false);
	const live = useRef(true);

	useEffect(() => {
		live.current = true;
		return () => {
			live.current = false;
		};
	}, []);

	// (What a contributed panel runs: the same call, with the values it holds.)
	const call = useCallback(
		async (sent: JsonObject) => jsonValue(await runOperation(api, operation, sent)),
		[api, operation],
	);

	const run = useCallback(async () => {
		setBusy(true);
		setAnswer(null);
		try {
			const value = await call(values);
			if (live.current) {
				setAnswer({ value });
			}
		} catch (error) {
			if (live.current) {
				setAnswer({ problem: saidBy(error) });
			}
		} finally {
			if (live.current) {
				setBusy(false);
			}
		}
	}, [call, values]);

	// (A list is what its page opens with: the operation runs itself, and its button runs it
	// again. A ref, so a field's value changing does not run it a second time.)
	const latest = useRef(run);
	latest.current = run;
	const isList = operation.ui.view === "list";
	useEffect(() => {
		if (isList) {
			void latest.current();
		}
	}, [isList]);

	const fields = fieldsOf(operation.request_schema).map((field) => field.name);
	const confirm =
		operation.ui.confirm_field === null
			? null
			: confirmation(operation.ui.confirm_field, values, fields);

	const go = async () => {
		if (operation.hints.destructive) {
			const about =
				confirm?.target === null || confirm === null
					? `${operation.title}?`
					: `${operation.title} ${confirm.target}?`;
			const yes = await ask(`${about} This cannot be undone.`, { ok: operation.title });
			if (!yes) {
				return;
			}
		}
		await run();
	};

	const widgets = useFieldWidgets(registry);

	// (The plugin's own panel for it, when it has one: udl's form and Run are not drawn at all.)
	if (contributed("operationViews", operation.name, registry)) {
		return (
			<article className="card bg-base-200">
				<div className="card-body">
					<PluginSlot
						point="operationViews"
						lookup={operation.name}
						registry={registry}
						replace
						props={{
							operation: guidedOf(operation),
							schema: operation.request_schema,
							run: call,
						}}
					/>
				</div>
			</article>
		);
	}

	return (
		<article className="card bg-base-200">
			<div className="card-body">
				<h4 className="card-title">
					{operation.title}
					{operation.hints.destructive && (
						<span className="badge badge-error badge-sm">destructive</span>
					)}
				</h4>
				{operation.summary !== "" && <p className="text-sm opacity-70">{operation.summary}</p>}
				{fields.length > 0 && (
					<SchemaForm
						schema={operation.request_schema}
						value={values}
						onChange={setValues}
						disabled={busy}
						widgets={widgets}
					/>
				)}
				<div className="card-actions items-center">
					<button
						type="button"
						className="btn btn-primary btn-sm"
						disabled={busy || (confirm !== null && !confirm.ok)}
						onClick={() => void go()}
					>
						{isList ? "Refresh" : "Run"}
					</button>
					{confirm !== null && !confirm.ok && (
						<span className="text-xs opacity-70">{mustSay(operation, confirm.target)}</span>
					)}
				</div>
				{answer !== null && "problem" in answer && (
					<div role="alert" className="alert alert-error">
						<span>
							{answer.problem.message}
							{answer.problem.hint !== null && (
								<span className="block text-xs opacity-80">{answer.problem.hint}</span>
							)}
						</span>
					</div>
				)}
				{answer !== null && "value" in answer && (
					<PluginSlot
						point="resultViews"
						lookup={operation.name}
						registry={registry}
						match="prefix"
						replace
						props={{ operation: guidedOf(operation), value: answer.value }}
						fallback={<ResultView schema={operation.response_schema} value={answer.value} />}
					/>
				)}
			</div>
		</article>
	);
}

/** What the page says while a destructive operation's confirm field is empty or wrong. */
function mustSay(operation: GeneratedOperation, target: string | null): string {
	const field = operation.ui.confirm_field ?? "the field";
	return target === null
		? `Type anything in ${field} to confirm.`
		: `Type ${target} in ${field} to confirm.`;
}
