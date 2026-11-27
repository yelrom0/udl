import { useMemo, useState } from "react";
import type { Runner } from "../model";
import type { StartChat } from "../stores/chat";

/** The folder a chat works in: what a person types, or the one it was started in. */
export interface NewChatProps {
	/** The harnesses that answered (an installed one may be chosen). */
	readonly runners: readonly Runner[];
	/** Whether udl answered at all (before it has, the form is drawn empty). */
	readonly started: boolean;
	onStart(request: StartChat): Promise<void>;
}

/** The host a runner is on, once each, in the order udl answered. */
function hostsOf(runners: readonly Runner[]): string[] {
	const seen: string[] = [];
	for (const one of runners) {
		if (!seen.includes(one.host)) {
			seen.push(one.host);
		}
	}
	return seen;
}

/**
 * A chat with no chat yet (29.12): whose harness, which model, where it runs and which folder it
 * works in. The runner is the one the person picked (or the first installed), and the folder is
 * the one they type — a chat works where a person works, so udl does not guess it.
 */
export function NewChat({ runners, started, onStart }: NewChatProps) {
	const installed = useMemo(() => runners.filter((one) => one.installed), [runners]);
	const [runner, setRunner] = useState("");
	const [model, setModel] = useState("");
	const [host, setHost] = useState("");
	const [directory, setDirectory] = useState("");
	const [starting, setStarting] = useState(false);

	const picked = installed.find((one) => one.runner === runner) ?? installed[0];
	const where = hostsOf(installed).includes(host) ? host : (picked?.host ?? "local");
	const models = picked?.models ?? [];

	const start = async (): Promise<void> => {
		if (picked === undefined) {
			return;
		}
		setStarting(true);
		try {
			await onStart({
				runner: picked.runner,
				host: where,
				directory: directory.trim(),
				model: model === "" ? "" : model,
			});
		} finally {
			setStarting(false);
		}
	};

	return (
		<form
			className="flex flex-col gap-3 p-3 text-sm"
			aria-label="A new chat"
			onSubmit={(event) => {
				event.preventDefault();
				void start();
			}}
		>
			<p className="opacity-70">
				A chat is your own harness, in a folder of your choosing: it works where you do, and it asks
				you before it acts.
			</p>
			<label className="flex flex-col gap-1">
				<span className="text-xs opacity-70">Harness</span>
				<select
					className="select select-bordered select-sm"
					aria-label="Harness"
					value={picked?.runner ?? ""}
					onChange={(event) => {
						setRunner(event.target.value);
						setModel("");
					}}
				>
					{installed.length === 0 && (
						<option value="">
							{started ? "No harness is installed" : "Reading the harnesses…"}
						</option>
					)}
					{installed.map((one) => (
						<option key={`${one.host}-${one.runner}`} value={one.runner}>
							{`${one.runner} on ${one.host}`}
						</option>
					))}
				</select>
			</label>
			<label className="flex flex-col gap-1">
				<span className="text-xs opacity-70">Model</span>
				<select
					className="select select-bordered select-sm"
					aria-label="Model"
					value={model}
					onChange={(event) => {
						setModel(event.target.value);
					}}
				>
					<option value="">The harness's own default</option>
					{models.map((one) => (
						<option key={one.id} value={one.id}>
							{one.name === "" ? one.id : one.name}
						</option>
					))}
				</select>
			</label>
			<label className="flex flex-col gap-1">
				<span className="text-xs opacity-70">Folder</span>
				<input
					className="input input-bordered input-sm font-mono"
					aria-label="Folder"
					placeholder="/work/udl"
					value={directory}
					onChange={(event) => {
						setDirectory(event.target.value);
					}}
				/>
			</label>
			<label className="flex flex-col gap-1">
				<span className="text-xs opacity-70">Host</span>
				<select
					className="select select-bordered select-sm"
					aria-label="Host"
					value={where}
					onChange={(event) => {
						setHost(event.target.value);
					}}
				>
					{hostsOf(installed).map((one) => (
						<option key={one} value={one}>
							{one}
						</option>
					))}
					{hostsOf(installed).length === 0 && <option value="local">local</option>}
				</select>
			</label>
			<button
				type="submit"
				className="btn btn-primary btn-sm self-end"
				disabled={picked === undefined || starting}
			>
				Start the chat
			</button>
		</form>
	);
}
