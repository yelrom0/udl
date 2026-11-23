import { useState } from "react";
import { messageOf } from "../json";
import { Markdown } from "../markdown";
import type { Note } from "../model";

/** The kinds of note a person may write (26.5): `system` is udl's own. */
const KINDS: readonly string[] = ["progress", "decision", "finding", "review"];

/** What `<Notes>` is given: the notes, and how to add one. */
export interface NotesProps {
	readonly notes: readonly Note[];
	/** Add one; given nothing, the timeline is read-only. */
	readonly onAdd?: ((note: { note_kind: string; text: string }) => Promise<void>) | undefined;
}

/** When a note was written, in the reader's own clock, to the minute. */
function atOf(at: string): string {
	const when = new Date(at);
	return Number.isNaN(when.getTime()) ? at : when.toLocaleString();
}

/**
 * A feature's or a task's notes (29.5), as a timeline: oldest first, each with who wrote it,
 * when and what kind. A box at the bottom adds one. The list's accessible name is "Notes",
 * which is how a page (and 29.7's proof) finds it.
 */
export function Notes({ notes, onAdd }: NotesProps) {
	const [kind, setKind] = useState("progress");
	const [text, setText] = useState("");
	const [busy, setBusy] = useState(false);
	const [error, setError] = useState<string | null>(null);
	const add = async (): Promise<void> => {
		if (onAdd === undefined || busy || text.trim() === "") {
			return;
		}
		setBusy(true);
		try {
			await onAdd({ note_kind: kind, text });
			setText("");
			setError(null);
		} catch (failure) {
			setError(messageOf(failure));
		} finally {
			setBusy(false);
		}
	};
	return (
		<section className="flex flex-col gap-2">
			<h2 className="text-sm font-semibold">Notes</h2>
			{error !== null && <p className="text-xs text-error">{error}</p>}
			{notes.length === 0 ? (
				<p className="text-sm opacity-70">No notes yet.</p>
			) : (
				<ol aria-label="Notes" className="flex flex-col gap-2">
					{notes.map((note) => (
						<li
							className="rounded-lg border border-base-content/20 p-2"
							key={`${note.at}-${note.author}-${note.text}`}
						>
							<p className="flex flex-wrap items-baseline gap-2 text-xs opacity-70">
								<span className="font-mono">{note.author}</span>
								<span className="badge badge-ghost badge-xs">{note.note_kind}</span>
								<span>{atOf(note.at)}</span>
							</p>
							<Markdown text={note.text} />
						</li>
					))}
				</ol>
			)}
			{onAdd !== undefined && (
				<div className="flex flex-col gap-2">
					<label className="flex items-center gap-2 text-xs">
						Kind
						<select
							aria-label="Kind"
							className="select select-xs select-bordered"
							onChange={(event) => {
								setKind(event.target.value);
							}}
							value={kind}
						>
							{KINDS.map((one) => (
								<option key={one} value={one}>
									{one}
								</option>
							))}
						</select>
					</label>
					<textarea
						aria-label="Note"
						className="textarea textarea-bordered min-h-20"
						onChange={(event) => {
							setText(event.target.value);
						}}
						placeholder="What was found, decided or done"
						value={text}
					/>
					<button
						className="btn btn-xs btn-outline self-start"
						disabled={busy || text.trim() === ""}
						onClick={() => {
							void add();
						}}
						type="button"
					>
						Add note
					</button>
				</div>
			)}
		</section>
	);
}
