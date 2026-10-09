#!/bin/zsh
set -e

cd "${0:A:h}"

python_bin=""
python_candidates=(
  "${NOTEAGENT_PYTHON:-}"
  "$(command -v python3 2>/dev/null)"
  "/opt/homebrew/bin/python3"
  "$HOME/miniconda3/bin/python3"
)

for candidate in "${python_candidates[@]}"; do
  if [[ -n "$candidate" && -x "$candidate" ]] && "$candidate" -c \
    'import sys; raise SystemExit(0 if sys.version_info >= (3, 10) else 1)' 2>/dev/null; then
    python_bin="$candidate"
    break
  fi
done

if [[ -z "$python_bin" ]]; then
  echo "NoteAgent needs Python 3.10 or newer. Install it from python.org, then run setup.command again."
  read "reply?Press Return to close…"
  exit 1
fi

echo "Using $($python_bin --version) from $python_bin"

"$python_bin" -m venv .venv
.venv/bin/python -m pip install --upgrade pip
.venv/bin/python -m pip install -r requirements.txt

echo
echo "NoteAgent is ready. Double-click run.command to start it."
read "reply?Press Return to close…"
