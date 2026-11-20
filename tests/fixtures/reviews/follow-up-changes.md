## Follow-up review: 28.2 Human actions (#48), after 7a09481

### Earlier items
1. **Actions never deleted: FIXED.** `_delete_actions` now runs from `delete_task`, `delete_feature` and `delete_project`. The project-wide delete means a re-created project's `A-1` no longer collides. There are tests for all three paths, including numbering starting over.
2. **Finishing a person's task doesn't wake `after` actions: FIXED.** `finish` now calls `become_now` after the move, and a test covers it.
3. **Partial write in `finish`: FIXED.** If the person's task is already `merged`, `finish` refuses before it writes the note. A test checks that nothing changes.
4. **`for_task` reuses a dropped action: FIXED.** It now reopens the action (`OPEN`, `result=None`), and a test covers it.
5. **`unassign` leaves the action with the old person: FIXED.** The open action's assignee is cleared, and a test covers it.
6. **`idempotent` hint on drop: FIXED.** A second drop now returns the action unchanged, and a test covers it.
7. **`task`/`after` not checked: FIXED.** Both are checked with `DoesNotExist`, and a test covers it.
8. **Nit, `human:` message: FIXED.** It now says "has no name", and a test covers it.
9. **Nit, "same record batch" wording: FIXED.** The note now says it is a second write, with no transaction yet (27.3's gap).
10. **Process, separate `test(...)` commits: NOT FIXED.** 7a09481 puts the tests and the fixes for all six items in one `fix(orch)` commit, so history still can't show the tests came first. The rest of the PR is fine. Please keep the test commits separate in the next round.

### New
- **NEW ISSUE (planning):** the "Review round 1" note for 28.2 has also been pasted under **27.4** (`planning/02-agent-orchastrator.md:869`), inside #43's notes. The correct copy is at line 949. Delete the copy at 869.
- **Nit:** `ActionRecords.delete_for_task` (`actions.py:110`) is now dead code and repeats `records._delete_actions`. Also, `delete_project` calls `_delete_actions` per task (`records.py:634`) after it has already deleted every action of the project (line 628).

**Verdict: changes needed**

