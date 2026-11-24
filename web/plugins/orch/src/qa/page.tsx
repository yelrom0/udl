import type { UIContext } from "@udl/ui-sdk";
import { useCallback, useEffect, useState } from "react";
import { messageOf } from "../json";
import { type QuestionThread, questionThreadOf } from "../model";
import { Page, Said } from "../page";
import { QuestionThreadView } from "./thread";

/**
 * One question, read for its thread (29.7): what was asked, what it is about, what has been said,
 * the answer, the follow-ups and the outcome.
 */
export function QuestionPageView({
	ctx,
	questionKey,
}: {
	readonly ctx: UIContext;
	readonly questionKey: string;
}) {
	const [found, setFound] = useState<QuestionThread | null>(null);
	const [error, setError] = useState<string | null>(null);

	const read = useCallback(async (): Promise<void> => {
		try {
			const value = await ctx.call("orch_get_question", { key: questionKey });
			setFound(questionThreadOf(value));
			setError(null);
		} catch (failure) {
			setError(messageOf(failure));
		}
	}, [ctx, questionKey]);

	useEffect(() => {
		void read();
	}, [read]);

	if (error !== null) {
		return (
			<Page title="Q&A">
				<p className="text-error" role="alert">
					{error}
				</p>
			</Page>
		);
	}
	if (found === null) {
		return (
			<Page title="Q&A">
				<Said>Reading the question…</Said>
			</Page>
		);
	}

	return (
		<Page title="Q&A">
			<QuestionThreadView ctx={ctx} found={found} project={found.question.project} refresh={read} />
		</Page>
	);
}
