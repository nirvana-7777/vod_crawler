#!/bin/bash
set -e

# Ensure runtime directories are owned by appuser even if a bind-mounted
# host volume was created as root (e.g. before you ran the manual chown).
# Safe to run every start — only touches ownership, not content.
for dir in /srv/vod-library /var/log/vod-crawler; do
    if [ -d "$dir" ]; then
        chown -R appuser:appuser "$dir" || true
    fi
done

# Drop from root to appuser for the actual application process.
# Unraid's Docker hooks (e.g. the Tailscale integration) run earlier,
# before this script's exec, and therefore still see root as required.
exec gosu appuser "$@"