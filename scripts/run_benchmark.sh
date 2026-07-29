#!/usr/bin/env bash
set -euo pipefail

project_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

if [[ -x "${project_root}/.venv/bin/python" ]]; then
  python_bin="${project_root}/.venv/bin/python"
elif [[ -x "${project_root}/venv/bin/python" ]]; then
  python_bin="${project_root}/venv/bin/python"
else
  python_bin="python3"
fi

cd "${project_root}"
exec "${python_bin}" benchmark_runner.py "$@"
