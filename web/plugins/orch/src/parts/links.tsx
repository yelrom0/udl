import { useState } from "react";
import { messageOf } from "../json";
import { LinkChip } from "../linkChip";
import type { Link } from "../model";

/** The kinds of link a person may add (26.4's own). */
const KINDS: readonly string[] = [
	"pull",
	"issue",
	"commit",
	"branch",
	"ci_run",
	"file",
	"url",
	"doc",
	"stitch",
	"task",
	"feature",
	"question",
];

/** What `<Links>` is given: what points at it, and how to add one. */
export interface LinksProps {
	readonly links: readonly Link[];
	/** Add one; given nothing, the list is read-only. */
	readonly onAdd?: ((link: { link_kind: string; url: string }) => Promise<void>) | undefined;
}

/**
 * What points at a feature or a task (29.5), one chip per link, in the kinds 26.4 knows: a pull
 * its number and its checks, a commit its SHA, a task its key. A box adds one by kind and URL.
 */
export function Links({ links, onAdd }: LinksProps) {
	const [kind, setKind] = useState("pull");
	const [url, setUrl] = useState("");
	const [busy, setBusy] = useState(false);
	const [error, setError] = useState<string | null>(null);
	const add = async (): Promise<void> => {
		if (onAdd === undefined || busy || url.trim() === "") {
			return;
		}
		setBusy(true);
		try {
			await onAdd({ link_kind: kind, url });
			setUrl("");
			setError(null);
		} catch (failure) {
			setError(messageOf(failure));
		} finally {
			setBusy(false);
		}
	};
	return (
		<section className="flex flex-col gap-2">
			<h2 className="text-sm font-semibold">Links</h2>
			{error !== null && <p className="text-xs text-error">{error}</p>}
			{links.length === 0 ? (
				<p className="text-sm opacity-70">No links yet.</p>
			) : (
				<ul aria-label="Links" className="flex flex-wrap gap-2">
					{links.map((link) => (
						<li key={`${link.link_kind}-${link.url}`}>
							<LinkChip
								kind={link.link_kind}
								state={link.state}
								title={link.title}
								url={link.url}
							/>
						</li>
					))}
				</ul>
			)}
			{onAdd !== undefined && (
				<div className="flex flex-wrap items-end gap-2">
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
					<input
						aria-label="URL"
						className="input input-xs input-bordered w-64"
						onChange={(event) => {
							setUrl(event.target.value);
						}}
						placeholder="https://…"
						type="text"
						value={url}
					/>
					<button
						className="btn btn-xs btn-outline"
						disabled={busy || url.trim() === ""}
						onClick={() => {
							void add();
						}}
						type="button"
					>
						Add link
					</button>
				</div>
			)}
		</section>
	);
}
