/**
 * ----------------
 * - report.tsx -
 * ----------------
 *
 * The import's report (29.9): `resultViews` for `orch_import_plan` (26.7), so the generated page
 * draws what the import made, changed and kept by key, and every line it could not place — with
 * its file, its line and what udl could not read of it — instead of a wall of JSON.
 */

import type { ResultViewProps } from "@udl/ui-sdk";
import { reportOf } from "../model";

/** One group of keys, under its own heading and count. */
function Keys({ title, keys }: { readonly title: string; readonly keys: readonly string[] }) {
	if (keys.length === 0) {
		return null;
	}
	return (
		<div>
			<h4 className="font-semibold text-sm">
				{title} {keys.length}
			</h4>
			<ul className="mt-1 flex flex-wrap gap-1">
				{keys.map((key) => (
					<li key={key} className="badge badge-ghost badge-sm font-mono">
						{key}
					</li>
				))}
			</ul>
		</div>
	);
}

/** The import's report: what it did, and the lines it could not place. */
export function ImportReportView({ value }: ResultViewProps) {
	const report = reportOf(value);
	return (
		<div className="card bg-base-100 shadow-sm" data-import-report="">
			<div className="card-body gap-3 p-4">
				<Keys title="Created" keys={report.created} />
				<Keys title="Updated" keys={report.updated} />
				<Keys title="Unchanged" keys={report.unchanged} />
				<Keys title="Kept" keys={report.kept} />
				{report.unplaced.length === 0 ? (
					<p className="text-base-content/60 text-sm">Every line was placed.</p>
				) : (
					<div>
						<h4 className="font-semibold text-sm">Not placed {report.unplaced.length}</h4>
						<ul className="mt-1 flex flex-col gap-1">
							{report.unplaced.map((line) => (
								<li
									key={`${line.source}:${line.line}`}
									className="rounded-box border border-base-300 p-2"
								>
									<span className="font-mono text-xs">
										{line.source}:{line.line}
									</span>
									<code className="mt-1 block text-xs">{line.text}</code>
									<span className="text-base-content/60 text-xs">{line.why}</span>
								</li>
							))}
						</ul>
					</div>
				)}
			</div>
		</div>
	);
}

export default ImportReportView;
