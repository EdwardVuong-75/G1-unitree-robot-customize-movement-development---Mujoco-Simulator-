#!/usr/bin/env bash
set -e

cd ~/mjhome_g1_welcome_project/voice_lan

if [ ! -f ./boya_mic_device.env ]; then
    echo "Missing boya_mic_device.env"
    echo "Run ./detect_boya_mic.sh first."
    exit 1
fi

source ./boya_mic_device.env

export LAPTOP_AGENT_URL="${LAPTOP_AGENT_URL:-http://192.168.123.100:5020}"
export NETWORK_INTERFACE="${NETWORK_INTERFACE:-eth0}"
export PORT="${PORT:-5012}"

export G1_WALK_CLI="${G1_WALK_CLI:-/home/unitree/unitree_sdk2_python/g1_walk_cli.py}"
export MJHOME_SAY_SCRIPT="${MJHOME_SAY_SCRIPT:-/home/unitree/unitree_sdk2_python/mjhome_say.py}"

echo "Starting robot voice frontend with:"
echo "  LAPTOP_AGENT_URL=$LAPTOP_AGENT_URL"
echo "  MIC_DEVICE=$MIC_DEVICE"
echo "  PORT=$PORT"

python3 robot_voice_frontend.py
