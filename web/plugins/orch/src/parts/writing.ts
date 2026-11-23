import type { UIContext } from "@udl/ui-sdk";
import { messageOf } from "../json";

/**
 * A write a page makes from a control that has no room for an error of its own (a state move):
 * udl's answer is said in a toast when it refuses, and the page is read again either way, so
 * what it draws is what udl holds.
 */
export async function wrote(
	ctx: UIContext,
	work: () => Promise<void>,
	refresh: () => Promise<void>,
): Promise<void> {
	try {
		await work();
	} catch (failure) {
		ctx.toast(messageOf(failure), "error");
	} finally {
		await refresh();
	}
}
