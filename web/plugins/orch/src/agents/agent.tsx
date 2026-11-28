import type { RouteProps } from "@udl/ui-sdk";
import type { Component } from "@udl/ui-sdk/react";
import { useState } from "react";
import { useAgent, useSession } from "../hooks";
import { asObject, asText, messageOf } from "../json";
import { Page, Said } from "../page";
import { AgentEditor } from "./editor";
import { Queue } from "./queue";
import { Transcript } from "./transcript";
import { whereOf } from "./tree";
import { usageLineOf, usageToneOf, usageWordsOf } from "./usage";

/** What a status looks like: its word and the dot's colour (DESIGN.md's own table). */
const DOTS: Readonly<Record<string, string>> = {
	working: "bg-success",
	waiting: "bg-info",
	limited: "bg-warning",
	offline: "bg-error",
	paused: "bg-neutral",
	idle: "bg-base-300",
};

/** `n` with thousands separators, as a person reads a count. */
function counted(n: number): string {
	return n.toLocaleString("en-AU");
}

/**
 * An agent (29.6): its status and what it runs on, its queue in udl's order (each task's size,
 * the current one's open pull), what that queue comes to in points and hours, its session —
 * what it has used, what it last said, and its transcript as its runner streams — and what a
 * person may do to it: pause or resume it, stop its session, give it a token, or edit it.
 */
export const AgentPage: Component<RouteProps> = ({ ctx, params, search }) => {
	const name = params.name ?? "";
	const { info, usage, agents, tasks, merge, progress, loaded, error, refresh } = useAgent(
		ctx,
		name,
	);
	const agent = info?.agent ?? null;
	// (A page opened from a task names the session it came from: a task's session is the one
	// that ran it, which may not be the agent's current one.)
	const current = info?.session ?? null;
	const chosen = search.session ?? current?.id ?? "";
	const { session: transcript } = useSession(ctx, chosen);
	const [editing, setEditing] = useState(false);
	const [token, setToken] = useState<string | null>(null);
	const [saying, setSaying] = useState("");
	const [busy, setBusy] = useState(false);
	const reading = !loaded && error === null;
	// (The card follows the session the page is showing: the current one, or one a task's page
	// sent it to. Its own answer is the current one's until the session's read comes back.)
	const session = transcript ?? current;
	const rate = progress.models.find((one) => one.model === agent?.model)?.hours_per_point ?? null;
	const write = async (operation: string, request: Record<string, string>): Promise<void> => {
		setBusy(true);
		try {
			await ctx.call(operation, request);
			await refresh();
		} catch (failure) {
			ctx.toast(messageOf(failure), "error");
		} finally {
			setBusy(false);
		}
	};
	return (
		<Page
			title={
				agent === null
					? name === ""
						? "Agent"
						: name
					: agent.title === ""
						? agent.name
						: agent.title
			}
		>
			{error !== null && <p className="text-error">{error}</p>}
			{reading && <Said>Reading the agent…</Said>}
			{agent !== null && info !== null && (
				<>
					<div className="flex flex-wrap items-baseline gap-2">
						<span className="font-mono text-sm">{agent.name}</span>
						<span
							aria-label={info.status === "" ? "idle" : info.status}
							className={`inline-block size-2 rounded-full ${DOTS[info.status] ?? DOTS.idle}`}
							role="img"
						/>
						<span className="text-xs">{info.status === "" ? "idle" : info.status}</span>
						<span className="badge badge-ghost badge-sm border-base-content/20">{agent.role}</span>
						<span className="text-xs opacity-70">
							{[agent.runner, agent.model, agent.host].filter((one) => one !== "").join(" · ")}
						</span>
						<span className="text-xs opacity-60">{`reports to ${whereOf(agents, agent.name)}`}</span>
					</div>
					<div className="flex flex-wrap gap-2">
						<button
							className="btn btn-xs btn-ghost"
							disabled={busy}
							onClick={() => {
								void write(agent.paused ? "orch_resume_agent" : "orch_pause_agent", {
									name: agent.name,
								});
							}}
							type="button"
						>
							{agent.paused ? "Resume" : "Pause"}
						</button>
						{session !== null && (
							<button
								className="btn btn-xs btn-ghost"
								disabled={busy}
								onClick={() => {
									void write("orch_stop_session", { session: session.id });
								}}
								type="button"
							>
								Stop its session
							</button>
						)}
						<button
							className="btn btn-xs btn-ghost"
							disabled={busy}
							onClick={() => {
								setBusy(true);
								void ctx
									.call("orch_issue_token", { agent: agent.name })
									.then((answer) => {
										setToken(asText(asObject(answer).token));
									})
									.catch((failure: unknown) => {
										ctx.toast(messageOf(failure), "error");
									})
									.finally(() => {
										setBusy(false);
									});
							}}
							type="button"
						>
							Issue a token
						</button>
						<button
							className="btn btn-xs btn-ghost"
							onClick={() => {
								setEditing(true);
							}}
							type="button"
						>
							Edit
						</button>
					</div>
					{token !== null && (
						<div className="rounded-lg border border-warning/40 bg-warning/10 p-2 text-xs">
							<p>Copy it now: it is shown once.</p>
							<p className="opacity-70">New token</p>
							<code className="font-mono break-all">{token}</code>
						</div>
					)}
					{editing && (
						<AgentEditor
							agent={agent}
							ctx={ctx}
							onDone={() => {
								setEditing(false);
								void refresh();
							}}
						/>
					)}
					<div className="grid gap-4 lg:grid-cols-3">
						<div className="flex flex-col gap-4 lg:col-span-2">
							<section aria-label="Its queue" className="flex flex-col gap-2">
								<h2 className="text-lg font-semibold">Queue</h2>
								<Queue
									agent={agent.name}
									ctx={ctx}
									entries={info.queue}
									hoursPerPoint={rate}
									merge={merge}
									refresh={refresh}
									tasks={tasks}
								/>
							</section>
							<section aria-label="Session" className="flex flex-col gap-2">
								<h2 className="text-lg font-semibold">Session</h2>
								{session === null ? (
									<p className="text-sm opacity-60">It is not in a session.</p>
								) : (
									<>
										{session.id !== current?.id && (
											<p className="text-xs opacity-60">
												Showing the session it ran that task in, not its current one.
											</p>
										)}
										<p className="flex flex-wrap gap-2 text-xs opacity-70">
											<span className="badge badge-ghost badge-sm border-base-content/20">
												{session.state}
											</span>
											<span>{`turn ${session.turns}`}</span>
											<span>{`${counted(session.tokens_in)} in · ${counted(session.tokens_out)} out`}</span>
											{session.cost !== null && <span>{`$${session.cost.toFixed(2)}`}</span>}
											{session.task !== "" && <span>{`on ${session.task}`}</span>}
										</p>
										<Transcript said={transcript?.events ?? []} />
										{session.id === current?.id && (
											<form
												className="flex flex-col gap-2"
												onSubmit={(event) => {
													event.preventDefault();
													const text = saying.trim();
													if (text === "") {
														return;
													}
													setBusy(true);
													void ctx
														.call("orch_send", { agent: agent.name, text })
														.then(() => {
															setSaying("");
															return refresh();
														})
														.catch((failure: unknown) => {
															ctx.toast(messageOf(failure), "error");
														})
														.finally(() => {
															setBusy(false);
														});
												}}
											>
												<label className="flex flex-col gap-1 text-xs" htmlFor="say-something">
													Say something to it
													<textarea
														className="textarea textarea-bordered textarea-sm min-h-20"
														id="say-something"
														onChange={(event) => {
															setSaying(event.target.value);
														}}
														value={saying}
													/>
												</label>
												<div>
													<button className="btn btn-sm btn-primary" disabled={busy} type="submit">
														Send
													</button>
												</div>
											</form>
										)}
									</>
								)}
							</section>
						</div>
						<div className="flex flex-col gap-4">
							{usage !== null && usageLineOf(usage) !== "" && (
								<section aria-label="Its usage" className="flex flex-col gap-1">
									<h2 className="text-lg font-semibold">Usage left</h2>
									<p className={`text-sm font-medium ${usageToneOf(usage)}`}>
										{usageLineOf(usage)}
									</p>
									{usageWordsOf(usage).map((words) => (
										<p className="text-xs opacity-70" key={words}>
											{words}
										</p>
									))}
								</section>
							)}
							<section aria-label="Its brief" className="flex flex-col gap-2">
								<h2 className="text-lg font-semibold">Brief</h2>
								<p className="whitespace-pre-wrap text-sm">
									{agent.brief === "" ? "It has no brief." : agent.brief}
								</p>
							</section>
							<section aria-label="Its powers" className="flex flex-col gap-1">
								<h2 className="text-lg font-semibold">Powers</h2>
								{agent.role === "manager" ? (
									<ul className="flex flex-col gap-1 text-sm">
										{(
											[
												["assign", "Assign"],
												["answer", "Answer"],
												["review", "Review"],
												["plan", "Plan"],
											] as const
										).map(([power, label]) => (
											<li key={power}>{`${agent.manages[power] ? "✓" : "—"} ${label}`}</li>
										))}
									</ul>
								) : (
									<p className="text-sm opacity-60">It is not a manager: it has no powers.</p>
								)}
							</section>
						</div>
					</div>
				</>
			)}
		</Page>
	);
};
