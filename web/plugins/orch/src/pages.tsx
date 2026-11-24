import type { RouteProps } from "@udl/ui-sdk";
import type { Component } from "@udl/ui-sdk/react";
import { AgentPage } from "./agents/agent";
import { AgentsPage } from "./agents/agents";
import { BoardPage } from "./board/board";
import { FeaturePage } from "./feature/feature";
import { InboxPage } from "./inbox/inbox";
import { Page, Said } from "./page";
import { QuestionPageView } from "./qa/page";
import { QaPage } from "./qa/qa";
import { TaskPage } from "./task/task";

/** Progress (29.11's page): what is merged, the burn-up and the forecast (27.10). */
const ProgressPage: Component<RouteProps> = () => (
	<Page title="Progress">
		<Said>Points merged, the burn-up, and when each feature is likely done.</Said>
	</Page>
);

/** The questions (29.7's page): what agents are asking, and what was decided. */
const QuestionsPage: Component<RouteProps> = (props) => <QaPage {...props} />;

/** A question (29.7's page): its thread, the answer, the follow-ups and the outcome. */
const QuestionPage: Component<RouteProps> = ({ ctx, params }) => (
	<QuestionPageView ctx={ctx} questionKey={params.key ?? ""} />
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
