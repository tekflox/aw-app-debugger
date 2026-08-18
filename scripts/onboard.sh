#!/usr/bin/env bash
# Onboard a NEW aw-app-* repo end to end: create it, make it public, grant it
# the two org-level things the release pipeline silently needs, push, and wait
# for the first release + catalog sync.
#
# Why this exists: every step below used to be manual, undocumented, and
# failed QUIETLY. A repo missing the runner-group grant queues a Release run
# that never picks up; a repo missing MARKETPLACE_SYNC_TOKEN fails at checkout
# with "Required secret not provided". Neither says "you forgot to onboard".
#
#   ./scripts/onboard.sh                 # infer app id + repo from aw-app.json
#   ./scripts/onboard.sh --org myorg     # different org
#   ./scripts/onboard.sh --dry-run       # print what it would do
#
# Needs: gh, authenticated, with org admin (admin:org) — the two grants are
# org-level API calls.
set -euo pipefail

ORG="tekflox"
RUNNER_GROUP_NAME="aw-private"
DRY_RUN=0

while [ $# -gt 0 ]; do
  case "$1" in
    --org) ORG="$2"; shift 2 ;;
    --runner-group) RUNNER_GROUP_NAME="$2"; shift 2 ;;
    --dry-run) DRY_RUN=1; shift ;;
    -h|--help) sed -n '2,18p' "$0"; exit 0 ;;
    *) echo "unknown arg: $1" >&2; exit 2 ;;
  esac
done

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

[ -f aw-app.json ] || { echo "no aw-app.json here — run from the app repo root" >&2; exit 1; }
APP_ID=$(python3 -c "import json;print(json.load(open('aw-app.json'))['id'])")
REPO_NAME=$(basename "$ROOT")
FULL="$ORG/$REPO_NAME"

echo "app id : $APP_ID"
echo "repo   : $FULL"
[ "$DRY_RUN" = "1" ] && { echo "(dry run — stopping)"; exit 0; }

run() { echo "+ $*"; "$@"; }

# 1. Repo. Public because the workspace fetches release tarballs from it, and
#    the marketplace catalog is public.
if gh repo view "$FULL" >/dev/null 2>&1; then
  echo "repo exists"
else
  run gh repo create "$FULL" --public \
      --description "$(python3 -c "import json;print(json.load(open('aw-app.json'))['description'][:250])")"
fi
gh repo edit "$FULL" --visibility public --accept-visibility-change-consequences >/dev/null 2>&1 || true

REPO_ID=$(gh api "repos/$FULL" --jq .id)

# 2. The org secret the reusable release workflow checks out with. Without it
#    the run dies at the first checkout step.
run gh api -X PUT "orgs/$ORG/actions/secrets/MARKETPLACE_SYNC_TOKEN/repositories/$REPO_ID"

# 3. The self-hosted runner group. `runs-on: [self-hosted, aw-baremetal]` only
#    matches if this repo is allowed to use the group holding those runners —
#    otherwise the job queues forever with no error.
GROUP_ID=$(gh api "orgs/$ORG/actions/runner-groups" \
  --jq ".runner_groups[] | select(.name==\"$RUNNER_GROUP_NAME\") | .id")
if [ -n "$GROUP_ID" ]; then
  run gh api -X PUT "orgs/$ORG/actions/runner-groups/$GROUP_ID/repositories/$REPO_ID"
else
  echo "WARN: runner group '$RUNNER_GROUP_NAME' not found — Release will queue forever" >&2
fi

# 4. Push.
git remote get-url origin >/dev/null 2>&1 || run git remote add origin "https://github.com/$FULL.git"
run git branch -M master
run git push -u origin master

# 5. Wait for the Release run, then point at the catalog PR. The sync PRs all
#    append to the end of the same array in apps.json, so a second one opened
#    before the first merges WILL conflict — merge them one at a time.
echo "waiting for the Release run…"
sleep 10
for _ in $(seq 1 60); do
  STATUS=$(gh run list --repo "$FULL" --workflow Release --limit 1 --json status -q '.[0].status' 2>/dev/null || echo "")
  [ "$STATUS" = "completed" ] && break
  sleep 15
done
gh run list --repo "$FULL" --workflow Release --limit 3 \
  --json status,conclusion,displayTitle -q '.[] | "\(.status) \(.conclusion // "-") — \(.displayTitle)"' || true

echo
echo "catalog sync PR (merge it — one at a time, they conflict with each other):"
gh pr list --repo "$ORG/aw-marketplace" --search "$APP_ID in:title" --limit 5 \
  --json number,title,url -q '.[] | "  #\(.number) \(.title)\n  \(.url)"' || true

cat <<EOF

Once that PR is merged the app is IN the catalog, which is what makes
\`signed\` true — there is no signing key anywhere in this system, "signed"
means "published in the official catalog" (src/apps/catalog.py::
is_marketplace_app). Only then are high-risk capabilities granted:
ui:code, containers:manage, config:extend:<app>.

Install it:  aw-workspace-cli marketplace install $APP_ID
EOF
