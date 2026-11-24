import { useState } from "react";
import { messageOf } from "../json";
import { Markdown } from "../markdown";

/** What `<SpecCard>` is given: its text, what to call it, and how to save a new one. */
export interface SpecCardProps {
	readonly title: string;
	readonly spec: string;
	/** Save `text`; given nothing, the spec is read-only. */
	readonly onSave?: ((text: string) => Promise<void>) | undefined;
}

/**
 * A feature's or a task's spec (29.5): markdown, and — when it is the reader's to change — an
 * Edit that turns it into a box. A save that udl refuses says why and keeps what was typed.
 */
export function SpecCard({ title, spec, onSave }: SpecCardProps) {
	const [editing, setEditing] = useState(false);
	const [text, setText] = useState(spec);
	const [busy, setBusy] = useState(false);
	const [error, setError] = useState<string | null>(null);
	const start = (): void => {
		setText(spec);
		setError(null);
		setEditing(true);
	};
	const save = async (): Promise<void> => {
		if (onSave === undefined || busy) {
			return;
		}
		setBusy(true);
		try {
			await onSave(text);
			setEditing(false);
			setError(null);
		} catch (failure) {
			setError(messageOf(failure));
		} finally {
			setBusy(false);
		}
	};
	return (
		<section
			aria-label={title}
			className="flex flex-col gap-2 rounded-lg border border-base-content/20 p-3 shadow-sm"
		>
			<header className="flex items-baseline justify-between gap-2">
				<h2 className="text-sm font-semibold">{title}</h2>
				{onSave !== undefined && !editing && (
					<button className="btn btn-xs btn-outline" onClick={start} type="button">
						Edit
					</button>
				)}
			</header>
			{error !== null && <p className="text-xs text-error">{error}</p>}
			{editing ? (
				<div className="flex flex-col gap-2">
					<textarea
						aria-label={title}
						className="textarea textarea-bordered min-h-40 font-mono text-xs"
						onChange={(event) => {
							setText(event.target.value);
						}}
						value={text}
					/>
					<div className="flex gap-2">
						<button
							className="btn btn-xs btn-primary"
							disabled={busy || text === spec}
							onClick={() => {
								void save();
							}}
							type="button"
						>
							Save
						</button>
						<button
							className="btn btn-xs btn-ghost"
							onClick={() => {
								setEditing(false);
								setError(null);
							}}
							type="button"
						>
							Cancel
						</button>
					</div>
				</div>
			) : spec.trim() === "" ? (
				<p className="text-sm opacity-70">No spec yet.</p>
			) : (
				<Markdown text={spec} />
			)}
		</section>
	);
}
