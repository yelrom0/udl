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

/** What a drop asks udl to do, and what it says when udl refuses it. */
export async function drop(
	ctx: UIContext,
	what: What | null,
	target: Target,
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
		const key = what.kind === "task" ? what.key : what.suggestion.task;
		if (target.kind === "features") {
			await ctx.call("orch_unassign", { key });
		} else {
			await ctx.call("orch_assign", {
				key,
				agent: target.agent,
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

/** An agent's queue set to exactly these keys, in this order. */
export async function reordered(
	ctx: UIContext,
	agent: string,
	keys: readonly string[],
	refresh: () => Promise<void>,
): Promise<void> {
	try {
		await ctx.call("orch_reorder_queue", { agent, keys: [...keys] });
	} catch (failure) {
		ctx.toast(messageOf(failure), "error");
	} finally {
		await refresh();
	}
}

/** A task assigned to an agent at the end of its queue (the "Assign to…" menu's own call). */
export async function assign(
	ctx: UIContext,
	key: string,
	agent: string,
	refresh: () => Promise<void>,
): Promise<void> {
	await drop(ctx, { kind: "task", key }, { kind: "agent", agent }, refresh);
}

/** A task taken out of its agent's queue, back to open (the features pane's own drop). */
export async function unassign(
	ctx: UIContext,
	key: string,
	refresh: () => Promise<void>,
): Promise<void> {
	await drop(ctx, { kind: "task", key }, { kind: "features" }, refresh);
}
