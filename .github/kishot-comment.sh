#!/usr/bin/env bash
# The kishot GitHub Action: draws the diff sheets of a board against the pull request's
# base, pushes them to a branch of their own and shows them in one comment, updated in
# place at each push. Called by action.yml, which sets the environment.
set -euo pipefail

if [ -z "${PR:-}" ]; then
  echo "kishot: this action runs on pull requests" >&2
  exit 1
fi

base=${BASE:-}
if [ -z "$base" ]; then
  git fetch --no-tags --depth 1 origin "+refs/heads/$BASE_REF:refs/remotes/origin/$BASE_REF"
  base="origin/$BASE_REF"
fi

out=$(mktemp -d)
wanted=()
for s in $SHEETS; do wanted+=(-s "$s"); done
read -ra extra <<< "$ARGS"
status=0
nix run "path:$KISHOT_ACTION" -- "$BOARD" --diff "$base" "${wanted[@]}" "${extra[@]}" \
  -o "$out/sheets" --json > "$out/report.json" || status=$?
{
  echo "report<<KISHOT_EOF"
  cat "$out/report.json"
  echo "KISHOT_EOF"
} >> "$GITHUB_OUTPUT"

# The images live on a branch of their own: a comment can only show what has a URL.
dir="pr-$PR/${HEAD_SHA:0:12}"
if compgen -G "$out/sheets/*" > /dev/null; then
  work=$(mktemp -d)
  git -C "$work" init -q
  git -C "$work" remote add origin "https://x-access-token:$GH_TOKEN@github.com/$GITHUB_REPOSITORY.git"
  if git -C "$work" fetch -q --depth 1 origin "$BRANCH" 2>/dev/null; then
    git -C "$work" checkout -q -b "$BRANCH" FETCH_HEAD
  else
    git -C "$work" checkout -q --orphan "$BRANCH"
  fi
  mkdir -p "$work/$dir"
  cp "$out"/sheets/* "$work/$dir/"
  git -C "$work" add -A
  if ! git -C "$work" diff --cached --quiet; then
    git -C "$work" -c user.name="github-actions[bot]" \
      -c user.email="41898282+github-actions[bot]@users.noreply.github.com" \
      commit -q -m "kishot: pull request #$PR at ${HEAD_SHA:0:12}"
    git -C "$work" push -q origin "HEAD:$BRANCH"
  fi
fi

body=$(python3 - "$out/report.json" "$GITHUB_REPOSITORY" "$BRANCH" "$dir" "$base" <<'EOF'
import json, sys, urllib.parse
from pathlib import Path

report, repo, branch, folder, base = sys.argv[1:]
try:
    data = json.loads(Path(report).read_text())
except ValueError:
    data = {"ok": False, "boards": []}
lines = ["<!-- kishot -->", f"### kishot: the board against `{base}`", ""]
for b in data["boards"]:
    lines.append(f"**{Path(b['board']).name}**")
    if b.get("error"):
        lines.append(f"> kishot failed: {b['error']}")
    for sheet, paths in b["sheets"].items():
        for p in paths:
            name = Path(p).name
            url = f"https://github.com/{repo}/blob/{branch}/{folder}/{urllib.parse.quote(name)}?raw=true"
            lines.append(f"<details open><summary>{sheet}</summary>\n\n![{name}]({url})\n</details>")
    lines.append("")
if not data["boards"]:
    lines.append("> kishot wrote no report: see the job's log.")
print("\n".join(lines))
EOF
)

existing=$(gh api --paginate "repos/$GITHUB_REPOSITORY/issues/$PR/comments" \
  --jq '.[] | select(.body | startswith("<!-- kishot -->")) | .id' | head -1)
if [ -n "$existing" ]; then
  gh api -X PATCH "repos/$GITHUB_REPOSITORY/issues/comments/$existing" -f body="$body" > /dev/null
else
  gh api -X POST "repos/$GITHUB_REPOSITORY/issues/$PR/comments" -f body="$body" > /dev/null
fi
exit "$status"
