#!/bin/zsh
set -e

cd "${0:A:h}"
if [[ ! -x .venv/bin/python ]]; then
  echo "NoteAgent has not been set up yet. Double-click setup.command first."
  read "reply?Press Return to close…"
  exit 1
fi

exec .venv/bin/python main.py
