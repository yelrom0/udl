import type { RouteProps, UIContext } from "@udl/ui-sdk";
import type { Component } from "@udl/ui-sdk/react";
import { useState } from "react";
import { useAgents } from "../hooks";
import type { AgentDetail, AgentInfo, AgentUsage } from "../model";
import { Page, Said } from "../page";
import { AgentEditor } from "./editor";
import { carryAgent, carryingAgent, dropped, reportsOf, rootsOf, whereOf } from "./tree";
import { usageLineOf, usageToneOf } from "./usage";

/** What a status looks like: its word and the dot's colour (DESIGN.md's own table). */
const DOTS: Readonly<Record<string, string>> = {
	working: "bg-success",
	waiting: "bg-info",
	limited: "bg-warning",
	offline: "bg-error",
	paused: "bg-neutral",
	idle: "bg-base-300",
};

/** What a row of the tree is given: the agent, the tree (for a drop's checks), its depth, and
 * what a person may do to it. */
interface NodeProps {
	readonly info: AgentInfo;
	readonly agents: readonly AgentInfo[];
	/** What each agent has left (27.14), by agent: the line under its name. */
	readonly usage: readonly AgentUsage[];
	readonly depth: number;
	readonly onEdit: (agent: AgentDetail) => void;
	readonly refresh: () => Promise<void>;
	readonly ctx: UIContext;
}

/** One agent, as the tree draws it: its status, its queue, and the agents that report to it. */
function Node({ info, agents, usage, depth, onEdit, refresh, ctx }: NodeProps) {
	const [over, setOver] = useState(false);
	const reports = reportsOf(agents, info.agent.name);
	const left = usage.find((one) => one.agent === info.agent.name) ?? null;
	const line = left === null ? "" : usageLineOf(left);
	return (
		<li
			aria-label={info.agent.name}
			className="flex flex-col gap-1"
			draggable
			onDragEnd={() => {
				carryAgent(null);
				setOver(false);
			}}
			onDragLeave={() => {
				setOver(false);
			}}
			onDragOver={(event) => {
				event.preventDefault();
				setOver(true);
			}}
			onDragStart={(event) => {
				// (A row inside a row: the drag is the one it started on, not its manager's.)
				event.stopPropagation();
				carryAgent(info.agent.name);
			}}
			onDrop={(event) => {
				// (This node's own drop, not the root's: one write, never two.)
				event.preventDefault();
				event.stopPropagation();
				setOver(false);
				const dragged = carryingAgent();
				if (dragged === null) {
					return;
				}
				void dropped(ctx, agents, dragged, info.agent.name).then((why) => {
					if (why !== null) {
						ctx.toast(why, "error");
					}
					return refresh();
				});
			}}
			style={{ marginLeft: `${depth * 16}px` }}
		>
			<div
				className={`flex flex-wrap items-center gap-2 rounded-lg border p-2 ${
					over ? "border-primary" : "border-base-content/20"
				}`}
			>
				<span
					aria-label={info.status === "" ? "idle" : info.status}
					className={`inline-block size-2 rounded-full ${DOTS[info.status] ?? DOTS.idle}`}
					role="img"
				/>
				<a
					className="link link-hover font-mono text-sm"
					href={`/ui/p/orch/agents/${encodeURIComponent(info.agent.name)}`}
				>
					{info.agent.name}
				</a>
				<span className="min-w-0 flex-1 truncate text-xs opacity-70">{info.agent.title}</span>
				<span className="badge badge-ghost badge-sm border-base-content/20">{info.agent.role}</span>
				<span className="text-xs opacity-60">
					{info.queue.length === 0 ? "nothing queued" : `${info.queue.length} queued`}
				</span>
				{line !== "" && left !== null && (
					<span className={`text-xs ${usageToneOf(left)}`}>{line}</span>
				)}
				<button
					className="btn btn-xs btn-ghost"
					onClick={() => {
						onEdit(info.agent);
					}}
					type="button"
				>
					Edit
				</button>
			</div>
			{reports.length > 0 && (
				<ul aria-label={`${info.agent.name}'s reports`} className="flex flex-col gap-1">
					{reports.map((name) => {
						const found = agents.find((one) => one.agent.name === name);
						return found === undefined ? null : (
							<Node
								agents={agents}
								ctx={ctx}
								depth={depth + 1}
								info={found}
								key={name}
								onEdit={onEdit}
								refresh={refresh}
								usage={usage}
							/>
						);
					})}
				</ul>
			)}
		</li>
	);
}

/**
 * The agents (29.6): who reports to whom, as a tree from the humans down, what each is doing
 * and what is queued for it. An agent dragged onto another reports to it; dragged onto the
 * humans, it reports to them — and a target that is not a manager, or one already below it, is
 * refused with the reason.
 */
export const AgentsPage: Component<RouteProps> = ({ ctx }) => {
	const { items, usage, loaded, error, refresh } = useAgents(ctx);
	const [editing, setEditing] = useState<AgentDetail | null>(null);
	const [making, setMaking] = useState(false);
	const reading = !loaded && error === null;
	const edit = (agent: AgentDetail): void => {
		setMaking(false);
		setEditing(agent);
	};
	return (
		<Page title="Agents">
			<Said>Who reports to whom, what each is doing, and the tasks queued for it.</Said>
			{error !== null && <p className="text-error">{error}</p>}
			{reading && <Said>Reading the agents…</Said>}
			<div className="flex flex-wrap gap-2">
				<button
					className="btn btn-sm btn-primary"
					onClick={() => {
						setEditing(null);
						setMaking(true);
					}}
					type="button"
				>
					New agent
				</button>
			</div>
			{(making || editing !== null) && (
				<AgentEditor
					agent={editing}
					ctx={ctx}
					onDone={() => {
						setMaking(false);
						setEditing(null);
						void refresh();
					}}
				/>
			)}
			{loaded && items.length === 0 && <Said>No agent yet: make one above.</Said>}
			{loaded && (
				<ul aria-label="The tree" className="flex flex-col gap-1">
					<li
						aria-label="Humans"
						className="flex flex-col gap-1"
						onDragOver={(event) => {
							event.preventDefault();
						}}
						onDrop={(event) => {
							event.preventDefault();
							const dragged = carryingAgent();
							if (dragged === null) {
								return;
							}
							void dropped(ctx, items, dragged, null).then((why) => {
								if (why !== null) {
									ctx.toast(why, "error");
								}
								return refresh();
							});
						}}
					>
						<div className="flex items-center gap-2 rounded-lg border border-base-content/20 bg-base-200/40 p-2">
							<span className="text-sm font-semibold">Humans</span>
							<span className="text-xs opacity-60">every agent may report here</span>
						</div>
						<ul aria-label="The humans' reports" className="flex flex-col gap-1">
							{rootsOf(items).map((name) => {
								const found = items.find((one) => one.agent.name === name);
								return found === undefined ? null : (
									<Node
										agents={items}
										ctx={ctx}
										depth={0}
										info={found}
										key={name}
										onEdit={edit}
										refresh={refresh}
										usage={usage}
									/>
								);
							})}
						</ul>
					</li>
				</ul>
			)}
			{loaded && items.length > 0 && (
				<p className="text-xs opacity-60">
					{items
						.map((one) => `${one.agent.name} reports to ${whereOf(items, one.agent.name)}`)
						.join(" · ")}
				</p>
			)}
		</Page>
	);
};
