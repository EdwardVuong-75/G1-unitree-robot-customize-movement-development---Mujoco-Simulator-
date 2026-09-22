#!/usr/bin/env bash
set -e
cd ~/mjhome_g1_welcome_project/final_console_v3

export PORT="${PORT:-5014}"
export LAPTOP_AGENT_URL="${LAPTOP_AGENT_URL:-http://192.168.123.100:5020}"
export OLD_CAMERA_BASE="${OLD_CAMERA_BASE:-http://127.0.0.1:5010}"
export OLD_CAMERA_AUTOSTART="${OLD_CAMERA_AUTOSTART:-1}"
export OLD_CAMERA_COMMAND="${OLD_CAMERA_COMMAND:-cd /home/unitree/mjhome_g1_welcome_project && PORT=5010 ./start.sh}"
export BOYA_MIC_DEVICE="${BOYA_MIC_DEVICE:-plughw:2,0}"
export LISTEN_SECONDS="${LISTEN_SECONDS:-3.2}"
export TTS_GAIN_DB="${TTS_GAIN_DB:-10}"

export NETWORK_INTERFACE="${NETWORK_INTERFACE:-eth0}"
export G1_WALK_CLI="${G1_WALK_CLI:-/home/unitree/unitree_sdk2_python/g1_walk_cli.py}"
export MJHOME_SAY_SCRIPT="${MJHOME_SAY_SCRIPT:-/home/unitree/unitree_sdk2_python/mjhome_say.py}"

python3 app_final_console_v3.py
