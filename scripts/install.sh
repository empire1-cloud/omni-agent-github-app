#!/usr/bin/env sh
# One-line install for the Omni-Agent CLI:
#   curl -fsSL https://raw.githubusercontent.com/empire1-cloud/omni-agent-github-app/main/scripts/install.sh | sh
# Installs the `omni-agent` command only. It does not touch any repository until you run it there.
set -eu

SOURCE="${OMNI_AGENT_SOURCE:-git+https://github.com/empire1-cloud/omni-agent-github-app.git}"

if command -v pipx >/dev/null 2>&1; then
  pipx install --force "$SOURCE"
elif command -v python3 >/dev/null 2>&1; then
  python3 -m pip install --user --upgrade "$SOURCE"
  echo "Installed with pip --user; make sure $(python3 -m site --user-base)/bin is on your PATH."
else
  echo "Omni-Agent needs Python 3.10+ (python3 not found)." >&2
  exit 1
fi

cat <<'DONE'

Omni-Agent is installed. Next:

  omni-agent demo                 # see a full run on a throwaway sample repo (offline)
  cd path/to/your/repo
  omni-agent quickstart           # detect write roots, seed example tasks, preview the first one

Nothing is written to your repo except .omni-agent/config.yaml and memory/tasks/inbox.md
until you run a task with --apply.
DONE
