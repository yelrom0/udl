import type { RouteProps } from "@udl/ui-sdk";
import type { Component } from "@udl/ui-sdk/react";
import { AgentPage } from "./agents/agent";
import { AgentsPage } from "./agents/agents";
import { BoardPage } from "./board/board";
import { FeaturePage } from "./feature/feature";
import { InboxPage } from "./inbox/inbox";
import { ActionsPageView, MergePageView } from "./merge/page";
import { Page, Said } from "./page";
import { ProgressPage } from "./progress/progress";
import { QuestionPageView } from "./qa/page";
import { QaPage } from "./qa/qa";
import { TaskPage } from "./task/task";

/** The questions (29.7's page): what agents are asking, and what was decided. */
const QuestionsPage: Component<RouteProps> = (props) => <QaPage {...props} />;

/** A question (29.7's page): its thread, the answer, the follow-ups and the outcome. */
const QuestionPage: Component<RouteProps> = ({ ctx, params }) => (
	<QuestionPageView ctx={ctx} questionKey={params.key ?? ""} />
);

/** Ready to merge (29.8's page): ready now, coming, held, and what merged lately — with its
 * merger (Q-8) and a Merge button per row. */
const MergePage: Component<RouteProps> = (props) => <MergePageView {...props} />;

/** To action (29.8's page): what only a person can do, each with its result box and Done. */
const ActionsPage: Component<RouteProps> = (props) => <ActionsPageView {...props} />;

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
