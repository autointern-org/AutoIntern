#!/bin/bash
set -euo pipefail

export PATH="/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin"

log_dir="${HOME}/Library/Logs"
log_file="${log_dir}/autointern-dispatch.log"
mkdir -p "$log_dir"

ts="$(date '+%Y-%m-%d %H:%M:%S')"
if gh workflow run internship-monitor.yml --repo autointern-org/AutoIntern; then
  echo "${ts} ok" >> "$log_file"
else
  echo "${ts} fail" >> "$log_file"
  exit 1
fi
