import type { UIContext } from "@udl/ui-sdk";
import { useId, useState } from "react";
import { useRunners } from "../hooks";
import { messageOf } from "../json";
import type { AgentDetail, Powers } from "../model";

/** What the editor is given: the agent it changes (null: a new one), and what to do when it is
 * saved or put away. The harnesses it offers are its own read (`orch_list_runners`). */
export interface AgentEditorProps {
	readonly ctx: UIContext;
	readonly agent: AgentDetail | null;
	readonly onDone: () => void;
}

/** What a new agent is: an implementer on the local host, with every power off. */
const NEW: AgentDetail = {
	name: "",
	title: "",
	runner: "",
	model: "",
	host: "local",
	role: "implementer",
	manager: null,
	manages: { assign: false, answer: false, review: false, plan: false },
	projects: [],
	brief: "",
	max_nudges: 3,
	enabled: true,
	paused: false,
};

/** The four powers a manager may be given, in the order the plan names them (O-D10). */
const POWERS: readonly { name: keyof Powers; label: string; said: string }[] = [
	{ name: "assign", label: "Assign", said: "Queue suggested tasks to its reports" },
	{ name: "answer", label: "Answer", said: "Answer its reports' questions" },
	{ name: "review", label: "Review", said: "Its OK is needed before a report's task is ready" },
	{ name: "plan", label: "Plan", said: "Create and split tasks within its reports' features" },
];

/** The roles a person may give an agent (27.9). */
const ROLES: readonly string[] = ["implementer", "manager", "reviewer"];

/** Each of a list, once, in the order it appears. */
function once(values: readonly string[]): string[] {
	return [...new Set(values)];
}

/**
 * An agent's editor (29.6): what it runs, where, which model, its role and its brief — and, for
 * a manager, the four powers its reports may be given. Who it reports to is the tree's drag,
 * not a field: a form would let it name a loop.
 */
export function AgentEditor({ ctx, agent, onDone }: AgentEditorProps) {
	const id = useId();
	const { items: runners, problems, answered } = useRunners(ctx);
	const editing = agent !== null;
	const [name, setName] = useState(agent?.name ?? NEW.name);
	const [title, setTitle] = useState(agent?.title ?? NEW.title);
	const [runner, setRunner] = useState(agent?.runner ?? NEW.runner);
	const [host, setHost] = useState(agent?.host ?? NEW.host);
	const [model, setModel] = useState(agent?.model ?? NEW.model);
	const [role, setRole] = useState(agent?.role ?? NEW.role);
	const [brief, setBrief] = useState(agent?.brief ?? NEW.brief);
	const [powers, setPowers] = useState<Powers>(agent?.manages ?? NEW.manages);
	const [saving, setSaving] = useState(false);
	// (The agent's own host is offered even when no host answered: a page does not rename where
	// an agent runs because the host it is on is not up.)
	const hosts = once([...runners.map((one) => one.host), ...(agent === null ? [] : [agent.host])]);
	const names = once(runners.map((one) => one.runner));
	const models = runners
		.filter((one) => one.runner === runner && one.host === host)
		.flatMap((one) => one.models);
	const save = async (): Promise<void> => {
		setSaving(true);
		try {
			await ctx.call("orch_set_agent", {
				name,
				title,
				runner,
				host,
				model,
				role,
				brief,
				manages: {
					assign: powers.assign,
					answer: powers.answer,
					review: powers.review,
					plan: powers.plan,
				},
			});
			onDone();
		} catch (failure) {
			ctx.toast(messageOf(failure), "error");
		} finally {
			setSaving(false);
		}
	};
	return (
		<form
			aria-label={editing ? `Edit ${agent.name}` : "New agent"}
			className="flex flex-col gap-3 rounded-xl border border-base-content/20 p-3"
			onSubmit={(event) => {
				event.preventDefault();
				void save();
			}}
		>
			<div className="grid gap-3 sm:grid-cols-2">
				<label className="flex flex-col gap-1 text-xs" htmlFor={`${id}-name`}>
					Name
					<input
						aria-label="Name"
						className="input input-bordered input-sm font-mono"
						disabled={editing}
						id={`${id}-name`}
						onChange={(event) => {
							setName(event.target.value);
						}}
						value={name}
					/>
				</label>
				<label className="flex flex-col gap-1 text-xs" htmlFor={`${id}-title`}>
					Title
					<input
						aria-label="Title"
						className="input input-bordered input-sm"
						id={`${id}-title`}
						onChange={(event) => {
							setTitle(event.target.value);
						}}
						value={title}
					/>
				</label>
				<label className="flex flex-col gap-1 text-xs" htmlFor={`${id}-runner`}>
					Runner
					{names.length === 0 ? (
						// (No host answered what it has installed — a headless one, or a host that is
						// not up yet. A select with nothing in it would be a dead end: a person may
						// still name the runner their agent runs on.)
						<input
							aria-label="Runner"
							className="input input-bordered input-sm"
							id={`${id}-runner`}
							onChange={(event) => {
								setRunner(event.target.value);
								setModel("");
							}}
							placeholder="claude-code"
							value={runner}
						/>
					) : (
						<select
							aria-label="Runner"
							className="select select-bordered select-sm"
							id={`${id}-runner`}
							onChange={(event) => {
								setRunner(event.target.value);
								setModel("");
							}}
							value={runner}
						>
							<option value="">Choose one…</option>
							{names.map((one) => (
								<option key={one} value={one}>
									{one}
								</option>
							))}
						</select>
					)}
				</label>
				<label className="flex flex-col gap-1 text-xs" htmlFor={`${id}-host`}>
					Host
					<select
						aria-label="Host"
						className="select select-bordered select-sm"
						id={`${id}-host`}
						onChange={(event) => {
							setHost(event.target.value);
						}}
						value={host}
					>
						{once([...hosts, "local"]).map((one) => (
							<option key={one} value={one}>
								{one}
							</option>
						))}
					</select>
				</label>
				<label className="flex flex-col gap-1 text-xs" htmlFor={`${id}-model`}>
					Model
					<select
						aria-label="Model"
						className="select select-bordered select-sm"
						id={`${id}-model`}
						onChange={(event) => {
							setModel(event.target.value);
						}}
						value={model}
					>
						<option value="">Its runner's default</option>
						{models.map((one) => (
							<option key={one.id} value={one.id}>
								{one.name}
							</option>
						))}
						{/* (A model its host did not list — one it runs today, or a host that is down — is
						still the truth about the agent: the select shows it rather than the default.) */}
						{model !== "" && !models.some((one) => one.id === model) && (
							<option value={model}>{model}</option>
						)}
					</select>
				</label>
				<label className="flex flex-col gap-1 text-xs" htmlFor={`${id}-role`}>
					Role
					<select
						aria-label="Role"
						className="select select-bordered select-sm"
						id={`${id}-role`}
						onChange={(event) => {
							setRole(event.target.value);
						}}
						value={role}
					>
						{ROLES.map((one) => (
							<option key={one} value={one}>
								{one}
							</option>
						))}
					</select>
				</label>
			</div>
			<label className="flex flex-col gap-1 text-xs" htmlFor={`${id}-brief`}>
				Brief
				<textarea
					aria-label="Brief"
					className="textarea textarea-bordered textarea-sm min-h-24"
					id={`${id}-brief`}
					onChange={(event) => {
						setBrief(event.target.value);
					}}
					value={brief}
				/>
			</label>
			{role === "manager" && (
				<fieldset className="flex flex-col gap-1">
					<legend className="text-xs opacity-70">What it may do for its reports</legend>
					{POWERS.map((one) => (
						<label className="flex items-center gap-2 text-sm" key={one.name}>
							<input
								checked={powers[one.name]}
								className="checkbox checkbox-sm"
								onChange={(event) => {
									setPowers({ ...powers, [one.name]: event.target.checked });
								}}
								type="checkbox"
							/>
							<span>{one.label}</span>
							<span className="text-xs opacity-60">{one.said}</span>
						</label>
					))}
				</fieldset>
			)}
			{answered && problems.length > 0 && (
				<p className="text-xs opacity-60">{`Some hosts could not be asked: ${problems.join("; ")}`}</p>
			)}
			<div className="flex gap-2">
				<button className="btn btn-sm btn-primary" disabled={saving} type="submit">
					Save
				</button>
				<button className="btn btn-sm btn-ghost" onClick={onDone} type="button">
					Cancel
				</button>
			</div>
		</form>
	);
}
