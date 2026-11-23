import type { RouteProps } from "@udl/ui-sdk";
import type { Component } from "@udl/ui-sdk/react";
import { useTask } from "../hooks";
import { KeyLink } from "../keyLink";
import { durationOf, isQuestionKey } from "../model";
import { Page, Said } from "../page";
import { Links } from "../parts/links";
import { Notes } from "../parts/notes";
import { SpecCard } from "../parts/spec";
import { wrote } from "../parts/writing";
import { SizeChip } from "../sizeChip";
import { Keys, PullCard, SessionLine, StateBox } from "./parts";

/**
 * A task (29.5): where it sits (a breadcrumb back to its feature), its spec, its state and the
 * moves a person may make, who holds it, what it needs and what it unblocks, its pull and
 * whether that may merge, what it is expected to take and has taken, every link, its notes as a
 * timeline, its questions and its session.
 */
export const TaskPage: Component<RouteProps> = ({ ctx, params }) => {
	const key = params.key ?? "";
	const { info, estimate, readiness, session, agents, error, refresh } = useTask(ctx, key);
	const task = info?.task ?? null;
	// (Every write names the project: the task's feature says which one it is.)
	const project = info?.feature?.project ?? "";
	const reading = info === null && error === null;
	return (
		<Page title={`Task ${key}`}>
			{error !== null && <p className="text-error">{error}</p>}
			{reading && <Said>Reading the task…</Said>}
			{task !== null && info !== null && (
				<>
					<nav aria-label="Breadcrumb" className="flex flex-wrap items-baseline gap-2 text-sm">
						{info.feature !== null ? (
							<a
								className="link link-hover"
								href={`/ui/p/orch/features/${encodeURIComponent(info.feature.key)}`}
							>
								{`Feature ${info.feature.key} ${info.feature.title}`}
							</a>
						) : (
							<span className="opacity-70">No feature</span>
						)}
						<span aria-hidden="true">/</span>
						<span className="font-mono">{task.key}</span>
					</nav>
					<div className="flex flex-wrap items-baseline gap-2">
						<h2 className="text-xl font-semibold">{task.title}</h2>
						<SizeChip size={task.size} />
						{estimate !== null && estimate.estimate !== null && (
							<span className="text-xs opacity-70">{`${durationOf(estimate.estimate)} expected`}</span>
						)}
						{estimate !== null && estimate.worked !== "" && (
							<span className="text-xs opacity-70">{`${durationOf(estimate.worked)} worked`}</span>
						)}
					</div>
					<div className="grid gap-4 lg:grid-cols-3">
						<div className="flex flex-col gap-4 lg:col-span-2">
							<SpecCard
								onSave={async (spec) => {
									await ctx.call("orch_set_task", { key, project, spec });
									await refresh();
								}}
								spec={task.spec}
								title="Spec"
							/>
							<Notes
								notes={info.notes}
								onAdd={async (note) => {
									await ctx.call("orch_add_note", {
										owner_kind: "task",
										owner: key,
										project,
										...note,
									});
									await refresh();
								}}
							/>
						</div>
						<div className="flex flex-col gap-4">
							<StateBox
								onMove={(to) =>
									wrote(
										ctx,
										async () => {
											await ctx.call("orch_move_task", { key, project, to });
										},
										refresh,
									)
								}
								state={task.state}
							/>
							<section className="flex flex-col gap-1">
								<h2 className="text-sm font-semibold">Assignee</h2>
								{task.assignee === null ? (
									<p className="text-sm opacity-70">Nobody.</p>
								) : agents.includes(task.assignee) ? (
									<a
										className="link link-hover text-sm"
										href={`/ui/p/orch/agents/${encodeURIComponent(task.assignee)}`}
									>
										{task.assignee}
									</a>
								) : (
									// (A person, `human:<name>`, or an agent udl no longer has: no page to go to.)
									<span className="text-sm">{task.assignee}</span>
								)}
								{task.blocked_on !== null && (
									<section
										aria-label="Waiting on"
										className="flex items-center gap-1 text-xs opacity-70"
									>
										Waiting on{" "}
										{isQuestionKey(task.blocked_on) ? (
											// (A question is a page to go to; a line of text is not.)
											<KeyLink kind="question" label={task.blocked_on} />
										) : (
											task.blocked_on
										)}
									</section>
								)}
								{task.hold !== null && <p className="text-xs opacity-70">{`Held: ${task.hold}`}</p>}
								{task.branch !== "" && (
									<p className="font-mono text-xs opacity-70">{task.branch}</p>
								)}
							</section>
							<Keys
								empty="Nothing: it waits on no other task."
								keys={task.needs}
								kind="task"
								name="Needs"
							/>
							<Keys empty="Nothing waits on it." keys={info.unblocks} kind="task" name="Unblocks" />
							<PullCard links={info.links} readiness={readiness} />
							<Links
								links={info.links}
								onAdd={async (link) => {
									await ctx.call("orch_add_link", {
										owner_kind: "task",
										owner: key,
										project,
										...link,
									});
									await refresh();
								}}
							/>
							<Keys
								empty="No questions about it."
								keys={info.questions.map((question) => question.key)}
								kind="question"
								name="Questions"
							/>
							<SessionLine session={session} />
						</div>
					</div>
				</>
			)}
		</Page>
	);
};
