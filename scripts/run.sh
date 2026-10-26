#!/usr/bin/env bash
set -euo pipefail
cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.."
case "${1:-ui}" in
  ui) exec /usr/bin/python3 -m leo.ui ;;
  daemon) exec /usr/bin/python3 -m leo.daemon ;;
  ctl) shift; exec /usr/bin/python3 -m leo.cli "$@" ;;
  *) printf 'Uso: scripts/run.sh [ui|daemon|ctl ...]\n' >&2; exit 2 ;;
esac
