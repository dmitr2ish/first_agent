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
"${python_bin}" -m pip check
"${python_bin}" -m compileall -q \
  benchmark_runner.py \
  visa_cli.py \
  visa_research \
  tests
"${python_bin}" -m unittest discover -s tests -v
