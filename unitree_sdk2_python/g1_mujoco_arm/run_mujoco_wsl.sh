#!/usr/bin/env bash
# Run in WSL after the G1 prints: rt/lowstate OK. HTTP bridge ready.
set -euo pipefail
cd "$(dirname "$0")"
exec python3 live_arm_to_g1.py --bridge http://192.168.123.164:5012 --dof 5
