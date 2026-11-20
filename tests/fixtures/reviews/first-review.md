## Review: 28.2 Human actions (#48)

**Overall: request changes.** The card is mostly covered: `HumanAction` with `A-<n>` keys from `Counters`, now/after/later and the `become_now` path, finish writing a note and merging a person's task, the four operations, and regenerated `openapi.json`/client. There are several correctness gaps in how actions follow their tasks.

### Issues
1. **Actions are never deleted** (`actions.py:110`). Nothing calls `delete_for_task`. `delete_task`, `delete_feature` and `delete_project` leave `orch_action` records behind. `delete_project` also deletes the `A-` counter, so a project made again under the same name collides on `(project, key)` when its first action is saved.
2. **Finishing a person's task doesn't wake actions that wait `after` it** (`actions.py:229-244`). A `human` task merges only through `finish`, and nothing calls `become_now` there, so those actions never become `now`.
3. **Partial write in `finish`.** The note is written before `tasks.move(..., "merged")`. If the task is already merged (a second action on the same human task, which `set_action(task=…)` allows), the move raises after the note is saved, and the action stays open.
4. **`for_task` reuses a dropped action** (`actions.py:274-276`). If the task is reassigned, the new person gets a dropped action that can't be finished, so the task can't reach `merged`.
5. **`unassign` leaves a human task's action open with the old person** (`queue.py`). They still see it under To action, and finishing it merges an `open`, unassigned task.
6. **`orch_drop_action` is hinted `idempotent=True`** (`plugin.py:229`), but a second drop raises NotAllowed.
7. **`task` and `after` aren't checked to exist** in `set_action`. A typo'd `after` never becomes `now`, and a typo'd `task` makes `finish` skip the note without saying so.
8. Nit: `_check_person` says "is an agent's name" for a bare `"human:"`.
9. Nit (planning note): it says the action is made in "the same record batch" as the assign. These are separate, non-transactional writes.
10. Process: the branch is one squashed commit, so I can't tell from history that the tests were written first. Please keep `test(...)` commits separate in the follow-up fixes, and write a failing test for each item above before fixing it.

### Positive
- `when`/`after` agreement and the `human:<name>` check are enforced in one place, with clear NotAllowed hints.
- The due order (now → after → later, then oldest first) is tested, and the "after turning into now" test the card asks for is there.
- Operations go through `service.call()` with typed request and answer models, and `HumanAction` is registered in `record_kinds()` with its test updated.
- `Action` is kept separate from the stored record, matching `_entry`/`QueueEntry`.

**Verdict: changes needed**

