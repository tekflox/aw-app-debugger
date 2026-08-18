# Examples

Copy-pasteable fragments for contribution surfaces the template's own
`aw-app.json` doesn't exercise. Each directory holds a **complete, valid
manifest** — not a snippet — so you can validate it before merging the
relevant part into your own:

```bash
python tests/validate_manifest.py examples/contributes-agents/aw-app.json
```

| Example | Surface | Reference |
|---|---|---|
| [`contributes-agents/`](./contributes-agents/) | `contributes.agents` — seed Agents Platform models, configs, groups and agents on install | [docs/contributing-agents.md](../docs/contributing-agents.md) |
| [`contributes-tasks/`](./contributes-tasks/) | `contributes.tasks` — seed scheduled tasks on install | [docs/contributing-tasks.md](../docs/contributing-tasks.md) |

Both surfaces are **seed-once**: an object is created only if nothing of
that identity exists, is never updated afterwards, and is never removed on
uninstall. Read the linked doc before shipping one — the "nothing is
updated, ever" rule is the part that surprises people on their second
release.
