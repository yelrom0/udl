import type { UIContext } from "@udl/ui-sdk";
import { messageOf } from "../json";
import type { Suggestion } from "../model";

/** What a drag is carrying: one task's key, or a whole suggestion. */
export type What =
	| { readonly kind: "task"; readonly key: string }
	| { readonly kind: "suggestion"; readonly suggestion: Suggestion };

/** Where it may be put down: an agent's column (at a position), or back in the features pane. */
export type Target =
	| { readonly kind: "agent"; readonly agent: string; readonly position?: number }
	| { readonly kind: "features" };

// (What a drag carries, for as long as it lasts. `dataTransfer` would be the browser's own, but a
// board is one person's page, and this works the same in every browser and in a test.)
let held: What | null = null;

/** What this drag carries (a card's `onDragStart`). */
export function carry(what: What | null): void {
	held = what;
}

/** What the last drag carried. */
export function carrying(): What | null {
	return held;
}

/** The task a drag carries, whichever kind it is. */
export function taskOf(what: What): string {
	return what.kind === "task" ? what.key : what.suggestion.task;
}

/** What a drop asks udl to do, and what it says when udl refuses it. */
export async function drop(
	ctx: UIContext,
	what: What | null,
	target: Target,
	project: string,
	refresh: () => Promise<void>,
): Promise<void> {
	if (what === null) {
		return;
	}
	try {
		if (what.kind === "suggestion" && what.suggestion.stack_on !== null) {
			// (Its need is `ready`, not merged: its branch stacks on that one's.)
			const stack = await ctx.confirm(`Stack on #${what.suggestion.stack_on}?`);
			if (!stack) {
				return;
			}
		}
		const key = taskOf(what);
		if (target.kind === "features") {
			await ctx.call("orch_unassign", { key, project });
		} else {
			await ctx.call("orch_assign", {
				key,
				agent: target.agent,
				project,
				...(target.position === undefined ? {} : { position: target.position }),
			});
		}
	} catch (failure) {
		// (A drop udl refuses snaps back — the board is read again — and the reason is said.)
		ctx.toast(messageOf(failure), "error");
	} finally {
		held = null;
		await refresh();
	}
}

/** A task put on an agent's queue by the menu or by "Queue it": the drop's own call, with no
 * drag behind it. */
export async function assigned(
	ctx: UIContext,
	key: string,
	agent: string,
	project: string,
	refresh: () => Promise<void>,
): Promise<void> {
	carry({ kind: "task", key });
	await drop(ctx, carrying(), { kind: "agent", agent }, project, refresh);
}

/** An agent's queue set to exactly these keys, in this order. */
export async function reordered(
	ctx: UIContext,
	agent: string,
	keys: readonly string[],
	project: string,
	refresh: () => Promise<void>,
): Promise<void> {
	try {
		await ctx.call("orch_reorder_queue", { agent, keys: [...keys], project });
	} catch (failure) {
		ctx.toast(messageOf(failure), "error");
	} finally {
		held = null;
		await refresh();
	}
}
