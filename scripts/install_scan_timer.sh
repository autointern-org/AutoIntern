#!/bin/bash
set -euo pipefail

src_dir="$(cd "$(dirname "$0")" && pwd)"
install_dir="${HOME}/.local/share/autointern"
plist_dir="${HOME}/Library/LaunchAgents"
log_dir="${HOME}/Library/Logs"
label="com.atinching.autointern.dispatch"
script="${install_dir}/dispatch_scan.sh"
plist="${plist_dir}/${label}.plist"

mkdir -p "$install_dir" "$plist_dir" "$log_dir"
cp "${src_dir}/dispatch_scan.sh" "$script"
chmod +x "$script"

sed -e "s|__DISPATCH_SCRIPT__|${script}|g" \
    -e "s|__HOME__|${HOME}|g" \
    "${src_dir}/com.atinching.autointern.dispatch.plist" > "$plist"

uid="$(id -u)"
launchctl bootout "gui/${uid}/${label}" 2>/dev/null || true

if ! launchctl bootstrap "gui/${uid}" "$plist"; then
  launchctl load -w "$plist"
fi

echo "installed and next fire is within 15 minutes"
