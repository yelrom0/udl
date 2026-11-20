/**
 * ----------------
 * - Operations.tsx -
 * ----------------
 *
 * A plugin's operations, as its page draws them (24.6): in their groups, a list's table run on
 * open with a refresh, a detail's or a form's fields with a Run, an action's button — and the
 * result under each, in 24.5's `<ResultView>`. What changes something is asked first; what udl
 * refuses says why, its hint included.
 */

import type { UdlApi } from "@udl/api";
import { fieldsOf, type JsonObject, type JsonValue } from "@udl/ui-sdk";
import { useCallback, useEffect, useRef, useState } from "react";
import { ask } from "../stores/confirms";
import { confirmation } from "./confirm";
import { ResultView } from "./ResultView";
import { jsonValue, runOperation, saidBy } from "./run";
import { SchemaForm } from "./SchemaForm";
import { type GeneratedOperation, type GeneratedPlugin, groupsOf } from "./spec";

/** What running an operation ended in: its answer, or what udl said. */
type Answer =
	| { readonly value: JsonValue }
	| { readonly problem: { readonly message: string; readonly hint: string | null } };

export interface OperationsTabProps {
	readonly plugin: GeneratedPlugin;
	readonly api: UdlApi;
}

/** The operations, in their groups (the ungrouped ones first). */
export function OperationsTab({ plugin, api }: OperationsTabProps) {
	const groups = groupsOf(plugin);
	return (
		<div className="flex flex-col gap-6">
			{groups.map((group) => (
				<section key={group.group ?? ""}>
					{group.group !== null && <h3 className="mb-2 text-lg font-semibold">{group.group}</h3>}
					<div className="flex flex-col gap-4">
						{group.operations.map((operation) => (
							<OperationCard key={operation.name} operation={operation} api={api} />
						))}
					</div>
				</section>
			))}
		</div>
	);
}

/** One operation: what it is, its fields (when it has any), and what it answered. */
export function OperationCard({
	operation,
	api,
}: {
	readonly operation: GeneratedOperation;
	readonly api: UdlApi;
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

	const run = useCallback(async () => {
		setBusy(true);
		setAnswer(null);
		try {
			const value = await runOperation(api, operation, values);
			if (live.current) {
				setAnswer({ value: jsonValue(value) });
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
	}, [api, operation, values]);

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
					<ResultView schema={operation.response_schema} value={answer.value} />
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
