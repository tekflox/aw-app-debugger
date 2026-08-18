# `contributes.agents` — ship the agents your app needs

An app that is *about* an agent — a domain reviewer, a vertical's coder, a
persona behind a chat channel — used to ship its skill and then rely on
somebody opening Agents Platform and hand-creating four rows in the right
order. Nothing in the install said so, and nothing said when they got it
wrong.

`contributes.agents` lets the app declare them instead. On install (and on
every boot, since activation re-runs) the workspace hands the declaration to
whichever installed app can reach Agents Platform — today
`aw-app-agents-platform-runners` — and it creates what isn't there.

A working example lives in [`examples/contributes-agents/`](../examples/contributes-agents/).

## The declaration

```jsonc
{
  "permissions": ["agents:contribute"],
  "contributes": {
    "agents": {
      "models":        [ /* Model        */ ],
      "agent_configs": [ /* AgentConfig  */ ],
      "groups":        [ /* AgentGroup   */ ],
      "agents":        [ /* Agent        */ ],
      "agent_flows":   [ /* AgentFlow    */ ]
    }
  }
}
```

**The key order is the creation order, and it is not cosmetic.** An Agent
references a model, an agent config and a group *by slug*, and an Agents
Flow's graph names agents by slug — Agents Platform stores all of those as
plain strings, so declaring an agent whose group doesn't exist yet doesn't
error, it produces an agent pointing at nothing. The provider always
creates models → configs → groups → agents → agent_flows, so your manifest
never has to think about it. Declare only the kinds you need; every key is
optional.

Every entry needs a **`slug`** — that is the identity of a seeded object
(see "Seeded, not owned" below), so the workspace rejects a manifest with a
missing, blank, or non-slug-shaped one at install time rather than seeding
a duplicate later. Beyond the slug: a `models` entry needs `provider` and
`model_id`; the other three need a `name`.

### Fields

Anything Agents Platform's own create schema accepts is passed through;
anything else is dropped before the POST (the platform 422s on unknown
fields, which would otherwise turn a future manifest-only key into a hard
seeding failure). The useful ones:

| Kind | Fields |
|---|---|
| `models` | `slug`, `provider` (`anthropic`/`openai`/`bedrock`/`cli`/`echo`/`fake`), `model_id`, `display_name` (defaults to the slug), `params`, `enabled` |
| `agent_configs` | `slug`, `name`, `description`, `mcp_servers` (preferred — see below), `mcp_config`, `extra_volumes`, `permissions`, `auto_compact_threshold_tokens` |
| `groups` | `slug`, `name`, `description`, `instructions`, `capabilities`, `kanban_target_status` |
| `agents` | `slug`, `name`, `description`, `system_prompt`, `model_slug`, `agent_config_slug`, `group_slug`, `skill_slugs`, `use_cases`, `capabilities`, `tool_specs`, `params`, `mcp_config`, `extra_volumes`, `permissions`, `inherit_from`, `hidden_from_flow`, `kanban_target_status`, `icon`, `color` |
| `agent_flows` | `slug`, `name`, `description`, `enabled`, `graph`, `max_hops`, `budget_tokens`, `budget_usd` |

### Agents Flows — the topology, not an execution DAG

An `agent_flows` entry is a *capability graph*: which agents may hand off
to which, starting from a `source` node (the inbound channel). It is not a
`Workflow` — nothing executes it. What it does is guidance: when a flow is
`enabled`, every agent appearing as a node in it gets the `aw-agents-flow`
skill plus the list of agents directly connected to it injected into its
system prompt at dispatch time. The agent can still call anyone; the list
is a map, not a fence.

```jsonc
{ "slug": "software-engineering", "name": "Agents Flow: Software Engineering",
  "enabled": true,
  "graph": {
    "nodes": [
      { "id": "source",           "type": "source", "label": "Source",
        "position": { "x": 40, "y": 240 } },
      { "id": "agent-architect",  "type": "agent", "agent_slug": "architect",
        "label": "Architect", "position": { "x": 560, "y": 140 } }
    ],
    "edges": [ { "id": "e1", "source": "source", "target": "agent-architect" } ]
  }
}
```

A node is `{id, type, label, position}` plus `agent_slug` (`type: "agent"`)
or `group_slug` (`type: "group"`). Exactly one `source` node, and its `id`
must be the literal `"source"` — that's what the flow editor expects when
it re-opens your graph. `position` is only for the editor's canvas, but
omit it and every node stacks at the origin the first time somebody opens
the flow. Edges are undirected in effect: the injected list is everything
adjacent to you, whichever end of the edge you sit on.

Ship the agents and the flow in the same manifest. A team of agents that
doesn't say how it connects is just a list of agents.

### Long prompts live in files

A system prompt does not belong inside JSON. Any `agents` entry may use
`system_prompt_file`, and any `groups` entry `instructions_file`, giving a
path **relative to your app's package dir**:

```jsonc
{ "slug": "sec-reviewer", "name": "Security Reviewer",
  "system_prompt_file": "prompts/sec-reviewer.md" }
```

The workspace inlines the file's contents before the declaration reaches the
provider. Paths are confined to your package; one that escapes it, or
doesn't exist, drops that single field with a warning rather than failing
the install — you get an agent with an empty prompt, not a dead app. An
inline `system_prompt` wins if you somehow declare both.

Pair this with `contributes.skills`: put the durable, versioned contract in
a SKILL.md and keep the `system_prompt` to the few lines that point at it.

## MCP servers: declare the name, never the credential

An agent that can't reach the workspace's MCP gateway has no knowledge
base, no Kanban and none of the Agents Flow terminal actions — for most
contributed agents that is the difference between working and not. But the
gateway entry is `{url, headers: {Authorization: Bearer <token>}}`, and a
manifest is a public artefact that ships to a marketplace.

So **an app never writes a token.** It names the server it wants:

```jsonc
"agent_configs": [
  { "slug": "my-app-config", "name": "My App",
    "mcp_servers": ["aw-gateway"] }
]
```

At install the workspace resolves that name against its own canonical
`.mcp.json` — the file the gateway app writes itself on boot — and
substitutes the real URL and bearer token before anything is POSTed. The
manifest carries an intention; the credential never leaves the machine, and
the person installing your app is never asked for a token they'd have no
way to obtain.

### The credential is refreshed; the content is not

This is the one place the seed-once rule below does **not** apply, and the
distinction is worth stating precisely:

| | On re-activation |
|---|---|
| `system_prompt`, `model_slug`, a flow `graph`, a name | **Left alone forever.** A user may have tuned it. |
| `mcp_config` that came from `mcp_servers` | **Re-asserted every time.** |

A bearer token is not content. Nobody typed it, nobody tuned it, and it
stops working the moment the gateway rotates it. Freezing it at first
install is how you get an agent whose config looks perfect in the UI and
has no MCP surface at all: the gateway 401s, the client registers **zero
tools**, and nothing anywhere logs it — the agent just behaves as though it
were bad at its job. That failure took a full day to find on 2026-08-14.

Only entries that used `mcp_servers` are refreshed, and only that one
field, so a prompt you edited in the UI survives. An app that spells
`mcp_config` out by hand owns it and is never touched.

### Rules of thumb

* **Use `mcp_servers`, not `mcp_config`.** Reach for the explicit form only
  if you genuinely need a server the workspace's `.mcp.json` doesn't
  describe — and then it is yours to keep working.
* **Don't pin the gateway's address.** Let resolution supply it. The host
  is spelled differently depending on who has to reach it (`127.0.0.1`,
  `aw-app-mcp-gateway`, the bridge gateway IP) and hardcoding one has
  already caused a silent outage.
* **A name that isn't in `.mcp.json` resolves to nothing** — the entry is
  dropped with a warning and the agent seeds without it. Check the install
  logs if an agent comes up toolless.

## Seeded, not owned

Identical to [`contributes.tasks`](./contributing-tasks.md), keyed on the
slug:

* an object with that slug already exists → **left completely alone**
* no object with that slug exists → **created**

Two consequences, both deliberate:

* **Nothing is updated, ever.** A corrected system prompt in a new app
  version does *not* reach an existing installation. Ship it under a new
  slug, or the user edits theirs. This matters more here than for tasks —
  an agent's prompt is exactly the field a user tunes for weeks, and an app
  re-asserting its own copy on every boot would erase that with no trace.
* **Nothing is removed on uninstall.** An agent that has run has sessions,
  runs and retro scores hanging off it. It stays, to be deleted
  deliberately by someone who can see what else it is attached to.

Because the slug is Agents Platform's own natural key, an agent the user
already created by hand is recognised as already-there rather than
duplicated.

## What can go wrong, and what happens

Seeding never fails an install. An app whose features work but whose agent
didn't land beats an app that refuses to install, so every failure below is
a log line:

| Situation | Outcome |
|---|---|
| No provider app installed yet | Declaration is **held** and replayed when one appears — and the provider sweeps every already-loaded app when it activates, so boot order doesn't matter. |
| Provider installed but not configured (no `agents_platform_token`) | Quiet skip, logged. Paste the token in the app's settings; the next activation seeds. |
| Agents Platform unreachable / 500 | That object is skipped, the rest still go. Retried on the next boot. |
| Slug taken (409) | Treated as already-there — that's the same outcome a pre-existing slug gets. |

To check what landed: `aw-workspace-cli logs` for the seeding lines, then
the Agents Platform UI.
