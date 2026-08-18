# `contributes.tasks` — ship the schedules your app depends on

Some features only work if something runs on a timer: a nightly cleanup, a
periodic sync, a weekly digest. Before this surface, the app installed fine
and the schedule was a line in a README that someone was supposed to
re-create by hand in the Tasks UI.

`contributes.tasks` lets the app declare them. On install (and on every
boot, since activation re-runs) the workspace hands each declaration to the
installed tasks app, which creates the ones that aren't there.

A working example lives in [`examples/contributes-tasks/`](../examples/contributes-tasks/).

## The declaration

```jsonc
{
  "permissions": ["tasks:contribute"],
  "contributes": {
    "tasks": [
      {
        "name": "Nightly index rebuild",
        "type": "agentic_output",
        "command": "myapp-cli reindex --quiet",
        "notify_exit_codes": [1, 2],
        "schedules": [{ "kind": "daily", "time": "03:00" }]
      }
    ]
  }
}
```

Three task types:

* **`agentic_output`** — runs a `command` and notifies you when it exits
  with one of `notify_exit_codes`. Cheap; nothing agentic runs unless the
  exit code says something is worth looking at.
* **`terminal`** — fires a `prompt` into a reusable CLI agent session.
* **`agent_prompt`** — dispatches a `prompt` to the Agents Platform agent
  named by `agent_slug`. This is the one to pair with
  [`contributes.agents`](contributing-agents.md): an app that ships an agent
  *and* the schedule driving it declares both, and the pair arrives together
  on install instead of needing someone to wire them up by hand.

<!-- agent_slug-required: agent_prompt, agentic_output -->
<!-- Parsed by tests/test_docs_match_the_validator.py and compared to
     aw-workspace's src/apps/manifest.py. Prose is for humans and is a
     poor thing to assert on — this line is the claim a test can check. -->

Fields: `name` (**required**, and the identity — see below), `type`
(default `terminal`), `command` (required for `agentic_output`), `prompt`
(required for `terminal` and `agent_prompt`), `agent_slug` (**required for
both `agent_prompt` AND `agentic_output`**), `reuse_session`, `cli_type`,
`notify_exit_codes` (list or comma-string), `schedules`, `enabled`.

An `agent_prompt` **or `agentic_output`** task without an `agent_slug` is
rejected at install time rather than seeded. `agentic_output` needs one for a
reason that isn't obvious: it only *notifies* through an agent, but the tasks
app's runner resolves that agent **before** running the command and bails with
"no agent_slug configured" if there is none — so the command never executes at
all. The alternative is a row that looks fine in the Tasks UI and then quietly
does nothing at 03:00 — this workspace's characteristic failure, and worth one
more validation to avoid.

This page said `agent_prompt` only until 2026-08-17, and an app built from it
had its install refused with a message about a field the docs never mentioned.
`tests/test_docs_match_the_validator.py` now fails if the two drift again.

`enabled` **defaults to `false`**, deliberately: a task that starts firing
the moment an app is installed is a surprise. The seeded schedule is a
suggestion the user opts into. Override it only if the task is genuinely
part of the app working at all.

### Schedule kinds

```jsonc
{ "kind": "once",    "at": "2026-09-01T03:00:00" }
{ "kind": "daily",   "time": "03:00" }
{ "kind": "weekly",  "days": [0, 3], "time": "09:00" }   // 0 = Monday
{ "kind": "monthly", "day_of_month": 1, "time": "06:00" }
{ "kind": "cron",    "expr": "*/15 * * * *" }
```

A task may carry several; it fires on whichever comes first. An empty
`schedules` list means manual-only.

## Seeded, not owned

The identity key is the **`name`**:

* a task with that name already exists → **left completely alone**
* no task with that name exists → **created**

Two consequences, both deliberate:

* **Nothing is updated, ever.** Shipping a corrected command in a new app
  version does *not* reach an existing installation. Ship it under a new
  name, or the user edits theirs. A schedule is something people tune —
  they disable it, move it an hour later, rewrite the command — and an app
  re-asserting its own version on every boot would silently undo that.
* **Nothing is removed on uninstall.** Unlike contributed skills, a seeded
  task belongs to the user the moment it exists. Uninstalling leaves it
  behind, enabled or not, to be removed deliberately.

Matching on the name also means a task the user created by hand *before*
installing your app is recognised as already-there rather than duplicated.

## What can go wrong, and what happens

Seeding never fails an install:

| Situation | Outcome |
|---|---|
| No tasks app installed yet | Declaration is **held** and replayed when one appears — and the tasks app sweeps every already-loaded app when it activates, so boot order doesn't matter. |
| One bad task in the list | Skipped and logged; the others still seed. |
| Task of that name exists | Left untouched. Not an error. |

See also [`contributes.agents`](./contributing-agents.md), which uses the
same seed-once contract for Agents Platform objects.
