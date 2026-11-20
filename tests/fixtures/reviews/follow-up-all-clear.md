## Follow-up review: 28.2 Human actions (#48), after dc1fd19

### Earlier items
1. Actions never deleted: **FIXED**. This round didn't change it.
2. Finishing a person's task doesn't wake `after` actions: **FIXED**. This round didn't change it.
3. Partial write in `finish`: **FIXED**. This round didn't change it.
4. `for_task` reuses a dropped action: **FIXED**. This round didn't change it.
5. `unassign` leaves the action with the old person: **FIXED**. This round didn't change it.
6. `idempotent` hint on drop: **FIXED**. This round didn't change it.
7. `task`/`after` not checked: **FIXED**. This round didn't change it.
8. Nit, `human:` message: **FIXED**. This round didn't change it.
9. Nit, "same record batch" wording: **FIXED**. This round didn't change it.
10. Process, separate `test(...)` commits: **FIXED for this round**. dc1fd19 is a docs-only `docs(orch)` commit, so it needs no test.
- Round 2, the note copied under 27.4: **FIXED**. This round didn't change it.
- Round 2 nits, dead `delete_for_task` and the redundant per-task delete: **FIXED**. This round didn't change them.
- Round 3 nit, the "Built" note listing `delete_for_task`: **FIXED**. The list is now `save_action`, `action`, `actions`. The note also says that deleting a task, feature or project deletes its actions in one place, `records._delete_actions`, which matches the code.

### New
Nothing new. This round only changes one line of planning text.

CI (`check`, `e2e`, `review`) was still pending when I checked. The PR body reports that `make check` passed locally at dc1fd19.

**Verdict: all clear**

