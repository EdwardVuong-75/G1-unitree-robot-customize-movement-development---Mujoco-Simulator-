#!/usr/bin/env bash
# Run ON the G1 (ssh unitree@192.168.123.164).
# 1) ping motion board  2) optional known-good wave  3) start HTTP arm bridge
set -euo pipefail
cd "$(dirname "$0")"

echo "=== ping motion board 192.168.123.161 ==="
if ! ping -c 2 -W 2 192.168.123.161; then
  echo "FAIL: motion board not reachable. Power / damping / development mode first."
  exit 1
fi

if [[ "${1:-}" == "--wave" ]]; then
  echo "=== known-good CLI (stop this before leaving the bridge running) ==="
  python3 ../g1_arm_cli.py eth0 high_wave || python3 /home/unitree/unitree_sdk2_python/g1_arm_cli.py eth0 high_wave
  echo "If the arms waved, press Enter to start the HTTP bridge (do not run CLI and bridge together)."
  read -r
fi

echo "=== g1_arm_bridge_server eth0 --dof 5 ==="
exec python3 g1_arm_bridge_server.py eth0 --dof 5 --yes
