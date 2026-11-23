import type { RouteProps } from "@udl/ui-sdk";
import type { Component } from "@udl/ui-sdk/react";
import { BoardPage } from "./board/board";
import { InboxPage } from "./inbox/inbox";
import { Page, Said } from "./page";

/** Progress (29.11's page): what is merged, the burn-up and the forecast (27.10). */
const ProgressPage: Component<RouteProps> = () => (
	<Page title="Progress">
		<Said>Points merged, the burn-up, and when each feature is likely done.</Said>
	</Page>
);

/** A feature (29.5's page): its spec, its tasks and its progress. */
const FeaturePage: Component<RouteProps> = ({ params }) => (
	<Page title={`Feature ${params.key ?? ""}`}>
		<Said>Its spec, its tasks in order, its links and its progress.</Said>
	</Page>
);

/** A task (29.5's page): its spec, where it is, its pull and its notes. */
const TaskPage: Component<RouteProps> = ({ params }) => (
	<Page title={`Task ${params.key ?? ""}`}>
		<Said>Its spec, its state, what it waits on, its pull and its notes.</Said>
	</Page>
);

/** The agents (29.6's page): the hierarchy, each one's status and queue. */
const AgentsPage: Component<RouteProps> = () => (
	<Page title="Agents">
		<Said>Who reports to whom, what each is doing, and the tasks queued for it.</Said>
	</Page>
);

/** An agent (29.6's page): its queue, its session and its transcript. */
const AgentPage: Component<RouteProps> = ({ params }) => (
	<Page title={params.name ?? "Agent"}>
		<Said>Its status, its queue, its session and what its runner is saying.</Said>
	</Page>
);

/** The questions (29.8's page): what agents are asking, and the thread of each. */
const QuestionsPage: Component<RouteProps> = () => (
	<Page title="Q&A">
		<Said>Every question an agent asked, the blocking ones first.</Said>
	</Page>
);

/** A question (29.8's page): its thread, and the answer. */
const QuestionPage: Component<RouteProps> = ({ params }) => (
	<Page title={`Question ${params.key ?? ""}`}>
		<Said>What was asked, what has been said, and the answer.</Said>
	</Page>
);

/** Ready to merge (29.9's page): what is ready, coming, held, and what merged lately. */
const MergePage: Component<RouteProps> = () => (
	<Page title="Ready to merge">
		<Said>Every pull that is ready, what is coming, what is held, and what merged lately.</Said>
	</Page>
);

/** To action (29.10's page): what only a person can do. */
const ActionsPage: Component<RouteProps> = () => (
	<Page title="To action">
		<Said>What only a person can do: now, then after, then later.</Said>
	</Page>
);

/**
 * Every page the manifest contributes, by its route: the mount the shell draws for
 * `/ui/p/orch/<route>`. A `$name` in a route is in the props' `params`.
 */
export const pages: Readonly<Record<string, Component<RouteProps>>> = {
	inbox: InboxPage,
	board: BoardPage,
	progress: ProgressPage,
	"features/$key": FeaturePage,
	"tasks/$key": TaskPage,
	agents: AgentsPage,
	"agents/$name": AgentPage,
	questions: QuestionsPage,
	"questions/$key": QuestionPage,
	merge: MergePage,
	actions: ActionsPage,
};
