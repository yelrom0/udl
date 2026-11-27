/**
 * ----------------
 * - machines.tsx -
 * ----------------
 *
 * The Machines view (29.9): `operationViews` for `orch_list_runners`, so the generated page's
 * Machines group draws a card per host instead of the list's rows — each harness's version and
 * whether it is a version behind, who it is signed in as and on what plan, what is wrong with it,
 * and the buttons that change it: install or update, set up, and sign in through 25.11's relay
 * (the harness's link, a box for the code it wants, and how it went).
 */

import type { JsonObject, JsonValue, OperationViewProps, UIContext } from "@udl/ui-sdk";
import { useCallback, useEffect, useState } from "react";
import {
	type HarnessState,
	type Machine,
	machinesOf,
	reportOfSignIn,
	runnerProblemsOf,
	type SignInReport,
} from "../model";

/** The operation a button runs, and what it takes. */
type Act = (name: string, request: JsonObject) => Promise<void>;

/** One harness's line in its host's card. */
function Harness({
	host,
	harness,
	act,
	onSignIn,
	busy,
}: {
	readonly host: string;
	readonly harness: HarnessState;
	readonly act: Act;
	readonly onSignIn: (host: string, runner: string) => void;
	readonly busy: string | null;
}) {
	const key = `${host}/${harness.runner}`;
	const working = busy === key;
	return (
		<li className="flex flex-col gap-1 border-base-300 border-t py-2 first:border-t-0">
			<div className="flex items-center gap-2">
				<span className="font-mono text-sm">{harness.runner}</span>
				<span className="text-base-content/60 text-xs">
					{harness.installed ? (harness.version ?? "?") : "not installed"}
				</span>
				{harness.update === null ? null : (
					<span className="badge badge-warning badge-sm">update available</span>
				)}
				<span className="ml-auto text-xs">
					{harness.signed_in
						? `signed in${harness.plan === null ? "" : ` (${harness.plan} plan)`}`
						: "not signed in"}
				</span>
				{harness.account === null ? null : (
					<span className="text-base-content/60 text-xs">as {harness.account}</span>
				)}
				{harness.guards ? null : <span className="badge badge-warning badge-sm">no guards</span>}
			</div>
			{harness.limited_until === null ? null : (
				<p className="text-warning text-xs">out of quota until {harness.limited_until}</p>
			)}
			{harness.problems.map((problem) => (
				<p key={problem} className="text-error text-xs">
					{problem}
				</p>
			))}
			{harness.detail === null ? null : (
				<p className="text-base-content/60 text-xs">{harness.detail}</p>
			)}
			<div className="flex flex-wrap gap-2">
				{harness.installed ? null : (
					<button
						type="button"
						className="btn btn-xs"
						disabled={working}
						onClick={() => void act("orch_runner_install", { host, runner: harness.runner })}
					>
						Install
					</button>
				)}
				{harness.update === null ? null : (
					<button
						type="button"
						className="btn btn-xs"
						disabled={working}
						onClick={() => void act("orch_runner_install", { host, runner: harness.runner })}
					>
						Update
					</button>
				)}
				<button
					type="button"
					className="btn btn-xs"
					disabled={working}
					onClick={() => void act("orch_runner_setup", { host, runner: harness.runner })}
				>
					Set up
				</button>
				{harness.signed_in ? null : (
					<button
						type="button"
						className="btn btn-xs btn-primary"
						disabled={working}
						onClick={() => onSignIn(host, harness.runner)}
					>
						Sign in
					</button>
				)}
			</div>
		</li>
	);
}

/** The relay, drawn while a sign-in runs: what the harness printed, and what it wants back. */
function Relay({
	signIn,
	act,
	onDone,
	onClose,
}: {
	readonly signIn: SignInReport;
	readonly act: Act;
	readonly onDone: () => void;
	readonly onClose: () => void;
}) {
	const [code, setCode] = useState("");
	const [said, setSaid] = useState<string | null>(null);

	useEffect(() => {
		if (!signIn.done) {
			return undefined;
		}
		if (signIn.ok === true) {
			onDone();
		} else if (signIn.ok === false) {
			setSaid("It ended without signing in.");
		}
		return undefined;
	}, [signIn, onDone]);

	// (The harness's output, not a page's: stop at what a URL cannot hold — a colour reset is a
	// control character, which is not whitespace — and drop the punctuation a sentence ends it
	// with.)
	const link =
		/https?:\/\/[^\s<>"'\p{Cc}]+/u.exec(signIn.prompt)?.[0]?.replace(/[.,;:!?)\]}]+$/, "") ?? null;
	return (
		<div className="mt-2 rounded-box border border-base-300 p-2">
			<p className="text-sm whitespace-pre-wrap">{signIn.prompt}</p>
			{link === null ? null : (
				<a className="btn btn-sm mt-1" href={link} target="_blank" rel="noopener noreferrer">
					Open the link
				</a>
			)}
			{signIn.command === null ? null : (
				<code className="mt-1 block text-xs">{signIn.command}</code>
			)}
			{signIn.expects === "code" ? (
				<div className="mt-2 flex items-end gap-2">
					<label className="form-control w-full">
						<span className="label-text text-xs">Code</span>
						<input
							className="input input-bordered input-sm w-full"
							value={code}
							onChange={(event) => setCode(event.target.value)}
						/>
					</label>
					<button
						type="button"
						className="btn btn-sm"
						disabled={code.trim() === ""}
						onClick={() => void act("orch_runner_login_reply", { id: signIn.id, text: code })}
					>
						Send
					</button>
				</div>
			) : null}
			<div className="mt-2 flex gap-2">
				<button
					type="button"
					className="btn btn-xs"
					onClick={() => void act("orch_runner_login_status", { id: signIn.id })}
				>
					Check
				</button>
				<button type="button" className="btn btn-ghost btn-xs" onClick={onClose}>
					Close
				</button>
			</div>
			{said === null ? null : <p className="text-error text-xs">{said}</p>}
		</div>
	);
}

/**
 * The Machines view: the hosts, each with its harnesses. It reads the list itself (the operation
 * is `orch_list_runners`) and reads it again after anything that changes a harness, so a card says
 * what is now true rather than what was.
 */
export function Machines({ ctx, run }: OperationViewProps & { readonly ctx: UIContext }) {
	const [answered, setAnswered] = useState<JsonValue | undefined>(undefined);
	const [busy, setBusy] = useState<string | null>(null);
	const [signIn, setSignIn] = useState<SignInReport | null>(null);
	const closeRelay = useCallback(() => setSignIn(null), []);

	const read = useCallback(async () => {
		setAnswered(await run({}));
	}, [run]);

	useEffect(() => {
		void read();
	}, [read]);

	const act = useCallback(
		async (name: string, request: JsonObject) => {
			const key = `${String(request.host ?? "")}/${String(request.runner ?? "")}`;
			setBusy(key);
			try {
				const answer = await ctx.call(name, request);
				if (name === "orch_runner_login") {
					setSignIn(reportOfSignIn(answer));
					return;
				}
				if (name === "orch_runner_login_reply") {
					// (The code is in: ask how it is going, since it may finish now.)
					setSignIn(reportOfSignIn(answer));
					const after = reportOfSignIn(
						await ctx.call("orch_runner_login_status", { id: String(request.id ?? "") }),
					);
					setSignIn(after);
					if (after?.done === true) {
						await read();
					}
					return;
				}
				if (name === "orch_runner_login_status") {
					const next = reportOfSignIn(answer);
					setSignIn(next);
					if (next?.done === true) {
						await read();
					}
					return;
				}
				ctx.toast(`${name === "orch_runner_install" ? "Installed" : "Set up"} ${key}`, "success");
				await read();
			} catch (error) {
				ctx.toast(error instanceof Error ? error.message : String(error), "error");
				await read();
			} finally {
				setBusy(null);
			}
		},
		[ctx, read],
	);

	// (While a sign-in runs, ask how it is going: a browser sign-in finishes on its own, and the
	// harness says so when it is asked. Keyed by the attempt's id and whether it has ended, so the
	// answers it brings do not ask again in a loop.)
	const attemptId = signIn?.id ?? null;
	const ended = signIn?.done ?? false;
	useEffect(() => {
		if (attemptId === null || ended) {
			return undefined;
		}
		let live = true;
		const check = async (): Promise<void> => {
			try {
				const next = reportOfSignIn(await ctx.call("orch_runner_login_status", { id: attemptId }));
				if (!live || next === null) {
					return;
				}
				setSignIn(next);
				if (next.done) {
					await read();
				}
			} catch (error) {
				// (The attempt expired, or the host went away: said once, and the asking stops.)
				if (!live) {
					return;
				}
				ctx.toast(error instanceof Error ? error.message : String(error), "error");
				// (Ended, so the asking stops: the attempt is gone, and there is nothing to ask.)
				setSignIn((previous) =>
					previous === null ? null : { ...previous, done: true, ok: false },
				);
			}
		};
		void check();
		const timer = setInterval(() => void check(), 2_000);
		return () => {
			live = false;
			clearInterval(timer);
		};
	}, [ctx, read, attemptId, ended]);

	const machines: Machine[] = machinesOf(answered);
	const problems = runnerProblemsOf(answered);

	return (
		<div className="card bg-base-100 shadow-sm" data-machines="">
			<div className="card-body gap-2 p-4">
				<div className="flex items-center gap-2">
					<h3 className="card-title text-base">Machines</h3>
					<span className="text-base-content/60 text-xs">
						{machines.length} {machines.length === 1 ? "host" : "hosts"}
					</span>
					<button
						type="button"
						className="btn btn-ghost btn-xs ml-auto"
						onClick={() => void read()}
					>
						Refresh
					</button>
				</div>
				{answered === undefined ? <p className="text-base-content/60 text-sm">Reading…</p> : null}
				{machines.map((machine) => (
					<section key={machine.host} className="rounded-box border border-base-300 p-2">
						<div className="flex items-center gap-2">
							<h4 className="font-semibold text-sm">{machine.host}</h4>
							<span className="text-base-content/60 text-xs">
								{machine.harnesses.length}{" "}
								{machine.harnesses.length === 1 ? "harness" : "harnesses"}
							</span>
						</div>
						<ul className="mt-1">
							{machine.harnesses.map((harness) => (
								<Harness
									key={harness.runner}
									host={machine.host}
									harness={harness}
									act={act}
									busy={busy}
									onSignIn={(host, runner) => void act("orch_runner_login", { host, runner })}
								/>
							))}
						</ul>
						{signIn !== null && signIn.host === machine.host ? (
							<Relay
								key={signIn.id}
								signIn={signIn}
								act={act}
								onDone={closeRelay}
								onClose={closeRelay}
							/>
						) : null}
					</section>
				))}
				{problems.length === 0 ? null : (
					<div className="text-base-content/60 text-xs">
						{problems.map((problem) => (
							<p key={problem}>{problem}</p>
						))}
					</div>
				)}
			</div>
		</div>
	);
}

export default Machines;

/** The props `react()` draws it with: the operation's, and the shell's context. */
export type MachinesProps = OperationViewProps & { readonly ctx: UIContext };
