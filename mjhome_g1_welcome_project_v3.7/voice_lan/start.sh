#!/usr/bin/env bash
set -e

cd "$(dirname "$0")"

export LAPTOP_AGENT_URL="${LAPTOP_AGENT_URL:-http://192.168.123.100:5020}"
export NETWORK_INTERFACE="${NETWORK_INTERFACE:-eth0}"
export MIC_DEVICE="${MIC_DEVICE:-default}"
export PORT="${PORT:-5012}"

export G1_WALK_CLI="${G1_WALK_CLI:-/home/unitree/unitree_sdk2_python/g1_walk_cli.py}"
export MJHOME_SAY_SCRIPT="${MJHOME_SAY_SCRIPT:-/home/unitree/unitree_sdk2_python/mjhome_say.py}"

python3 robot_voice_frontend.py
