import { counted } from "../format";
import { Markdown } from "../markdown";
import type { Spoken } from "../model";

/** What `<Transcript>` is given: what the session's runner said, in order. */
export interface TranscriptProps {
	readonly said: readonly Spoken[];
}

/** A run of text, as one paragraph: a runner streams a line in fragments. */
interface Run {
	readonly turn: number;
	readonly text: string;
}

/** `said` grouped: consecutive text of one turn is one paragraph, everything else stands alone. */
function runsOf(said: readonly Spoken[]): (Run | Spoken)[] {
	const runs: (Run | Spoken)[] = [];
	for (const one of said) {
		if (one.kind !== "text_delta") {
			runs.push(one);
			continue;
		}
		const last = runs.at(-1);
		if (last !== undefined && "text" in last && !("kind" in last) && last.turn === one.turn) {
			runs[runs.length - 1] = { turn: last.turn, text: last.text + one.text };
		} else {
			runs.push({ turn: one.turn, text: one.text });
		}
	}
	return runs;
}

/** Whether `one` is a run of text rather than one thing said. */
function isRun(one: Run | Spoken): one is Run {
	return !("kind" in one);
}

/** A moment udl sent, as a person reads it here (their own clock, to the minute). */
function when(at: string): string {
	const read = new Date(at);
	return Number.isNaN(read.getTime())
		? at
		: read.toLocaleString("en-AU", { dateStyle: "short", timeStyle: "short" });
}

/**
 * What a session's runner said (29.6), as a transcript: its text as markdown, each tool it
 * used, what a turn cost, and how a turn ended. The shell re-reads the session as the runner
 * streams, so this is drawn again per line.
 */
export function Transcript({ said }: TranscriptProps) {
	if (said.length === 0) {
		return <p className="text-sm opacity-60">Nothing said yet.</p>;
	}
	return (
		<ol aria-label="Transcript" className="flex flex-col gap-2 text-sm">
			{runsOf(said).map((one, at) => (
				<li className="flex flex-col gap-1" key={`${one.turn}-${at}`}>
					{isRun(one) ? <Markdown text={one.text} /> : <SaidLine said={one} />}
				</li>
			))}
		</ol>
	);
}

/** One thing a runner said that is not a run of text. */
function SaidLine({ said }: { readonly said: Spoken }) {
	switch (said.kind) {
		case "tool_started":
			return (
				<p className="font-mono text-xs opacity-80">
					{said.summary === "" ? `▸ ${said.name}` : `▸ ${said.name} — ${said.summary}`}
				</p>
			);
		case "tool_finished":
			return (
				<p className={`font-mono text-xs ${said.ok === false ? "text-error" : "opacity-60"}`}>
					{`${said.ok === false ? "✗" : "✓"} ${said.name}`}
				</p>
			);
		case "usage":
			return (
				<p className="text-xs opacity-60">
					{`${counted(said.tokens_in)} in · ${counted(said.tokens_out)} out${
						said.cost === null ? "" : ` · $${said.cost.toFixed(2)}`
					}`}
				</p>
			);
		case "turn_ended":
			return (
				<p className="text-xs opacity-60">
					{`Turn ${said.turn} ended${said.reason === "" ? "" : ` (${said.reason})`}${
						said.text === "" ? "" : `: ${said.text}`
					}`}
				</p>
			);
		case "runner_failed":
			// (The runner's own message, which is what it said went wrong.)
			return (
				<p className="text-xs text-error">{said.message === "" ? "It failed." : said.message}</p>
			);
		case "runner_limited":
			return (
				<p className="text-xs text-warning">
					{said.until === null ? "" : `Out of quota until ${when(said.until)}. `}
					{said.message}
				</p>
			);
		default:
			// (A kind this page does not know is still a line: it is not nothing.)
			return (
				<p className="font-mono text-xs opacity-60">
					{said.text === "" ? said.kind : `${said.kind}: ${said.text}`}
				</p>
			);
	}
}
