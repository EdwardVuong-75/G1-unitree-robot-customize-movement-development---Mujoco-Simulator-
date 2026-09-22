#!/usr/bin/env bash
set -e

cd ~/mjhome_g1_welcome_project/boya_voice_full

export BOYA_MIC_DEVICE="${BOYA_MIC_DEVICE:-plughw:2,0}"
export LAPTOP_AGENT_URL="${LAPTOP_AGENT_URL:-http://192.168.123.100:5020}"
export NETWORK_INTERFACE="${NETWORK_INTERFACE:-eth0}"
export PORT="${PORT:-5013}"

export G1_WALK_CLI="${G1_WALK_CLI:-/home/unitree/unitree_sdk2_python/g1_walk_cli.py}"
export MJHOME_SAY_SCRIPT="${MJHOME_SAY_SCRIPT:-/home/unitree/unitree_sdk2_python/mjhome_say.py}"

echo "Starting BOYA full server:"
echo "  BOYA_MIC_DEVICE=$BOYA_MIC_DEVICE"
echo "  LAPTOP_AGENT_URL=$LAPTOP_AGENT_URL"
echo "  PORT=$PORT"

python3 boya_voice_server.py
