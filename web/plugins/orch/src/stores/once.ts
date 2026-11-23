import type { UIContext } from "@udl/ui-sdk";

/** A store's reads, one at a time: ask for one as often as you like, and get one. */
export interface Reads {
	/** Ask for a read. A read already in flight is finished first, and everything asked for
	 * meanwhile is one read after it. Resolves when the read that covers this ask has finished. */
	ask(): Promise<void>;
	/** Stop asking: nothing new starts (a read already in flight is left to finish). */
	stop(): void;
}

/** A read someone is waiting for: the ask's number, and what resolves it. */
interface Waiter {
	readonly asked: number;
	readonly done: () => void;
}

/**
 * `run` one at a time, and once for everything asked while it runs: the shell publishes an event
 * per line a runner streams, and a read per event would read GitHub hundreds of times a minute —
 * and an older answer landing after a newer one would draw the past over the present. One read
 * at a time means the answer on screen is the one last asked for.
 *
 * The first ask starts on a microtask, so a burst of events published together (a dispatch, a
 * replay) is one read rather than one each.
 */
export function coalesced(run: () => Promise<void>, failed?: (error: unknown) => void): Reads {
	let stopped = false;
	let busy = false;
	let asked = 0;
	let covered = 0;
	const waiting: Waiter[] = [];
	const loop = async (): Promise<void> => {
		try {
			while (!stopped && covered < asked) {
				// (Everything asked for before this read starts is what it answers.)
				const upto = asked;
				try {
					await run();
				} catch (error) {
					// (A read that fails says so where it is drawn; it must not also be an
					// unhandled rejection, and it must not stop the next read.)
					failed?.(error);
				}
				covered = upto;
				for (const waiter of waiting.filter((one) => one.asked <= covered)) {
					waiter.done();
				}
				waiting.splice(0, waiting.length, ...waiting.filter((one) => one.asked > covered));
			}
		} finally {
			busy = false;
			// (Asked for, but never read: stopped first. Nothing waits on it forever.)
			for (const waiter of waiting.splice(0)) {
				waiter.done();
			}
		}
	};
	const ask = (): Promise<void> => {
		if (stopped) {
			return Promise.resolve();
		}
		asked += 1;
		const done = new Promise<void>((resolve) => {
			waiting.push({ asked, done: resolve });
		});
		if (!busy) {
			busy = true;
			void Promise.resolve().then(loop);
		}
		return done;
	};
	return {
		ask,
		stop: () => {
			stopped = true;
		},
	};
}

/** What a store subscribes with: each pattern, one subscription, its callback. */
export function on(ctx: UIContext, patterns: readonly string[], heard: () => void): (() => void)[] {
	return patterns.map((pattern) => ctx.events.subscribe(pattern, heard));
}
