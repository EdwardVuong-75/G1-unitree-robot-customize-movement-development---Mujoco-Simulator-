#!/usr/bin/env bash
set -e
cd ~/mjhome_g1_welcome_project/final_console

export PORT="${PORT:-5014}"
export LAPTOP_AGENT_URL="${LAPTOP_AGENT_URL:-http://192.168.123.100:5020}"
export BOYA_MIC_DEVICE="${BOYA_MIC_DEVICE:-plughw:2,0}"
export RGB_CAMERA_ID="${RGB_CAMERA_ID:-3}"
export LISTEN_SECONDS="${LISTEN_SECONDS:-2.8}"
export TTS_GAIN_DB="${TTS_GAIN_DB:-10}"

export NETWORK_INTERFACE="${NETWORK_INTERFACE:-eth0}"
export G1_WALK_CLI="${G1_WALK_CLI:-/home/unitree/unitree_sdk2_python/g1_walk_cli.py}"
export MJHOME_SAY_SCRIPT="${MJHOME_SAY_SCRIPT:-/home/unitree/unitree_sdk2_python/mjhome_say.py}"

python3 app_final_console.py
