import type { AgentLine } from "../model";

/** What an agent's state looks like: the dot's colour (DESIGN.md's own table). */
const DOTS: Readonly<Record<string, string>> = {
	working: "bg-success",
	waiting: "bg-info",
	limited: "bg-warning",
	offline: "bg-error",
	paused: "bg-neutral",
	idle: "bg-base-300",
};

/** One agent, in the strip: its dot and state, its name, and what it is on. */
function Agent({ line }: { readonly line: AgentLine }) {
	return (
		<div className="flex flex-col gap-1 rounded-lg border border-base-content/20 p-3">
			<div className="flex items-center gap-2">
				<span
					aria-label={line.state === "" ? "idle" : line.state}
					className={`inline-block size-2 rounded-full ${DOTS[line.state] ?? DOTS.idle}`}
					role="img"
				/>
				<span className="font-medium text-sm">{line.name}</span>
			</div>
			<p className="truncate text-xs opacity-70">{line.line !== "" ? line.line : line.title}</p>
		</div>
	);
}

/**
 * What every agent is doing, beside what waits on a person (28.4): its state as a dot, its name,
 * and the task it is on (or what its runner last said).
 */
export function AgentsStrip({ agents }: { readonly agents: readonly AgentLine[] }) {
	if (agents.length === 0) {
		return null;
	}
	return (
		<section aria-label="Agents" className="flex flex-col gap-2">
			<h2 className="text-xs font-semibold uppercase tracking-wide opacity-60">Agents</h2>
			<div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
				{agents.map((line) => (
					<Agent key={line.name} line={line} />
				))}
			</div>
		</section>
	);
}
