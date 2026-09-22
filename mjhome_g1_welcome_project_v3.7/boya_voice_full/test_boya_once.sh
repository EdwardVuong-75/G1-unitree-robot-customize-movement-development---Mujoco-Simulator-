#!/usr/bin/env bash
set -e

cd ~/mjhome_g1_welcome_project/boya_voice_full

export BOYA_MIC_DEVICE="${BOYA_MIC_DEVICE:-plughw:2,0}"
export LAPTOP_AGENT_URL="${LAPTOP_AGENT_URL:-http://192.168.123.100:5020}"

echo "Testing BOYA once:"
echo "  BOYA_MIC_DEVICE=$BOYA_MIC_DEVICE"
echo "  LAPTOP_AGENT_URL=$LAPTOP_AGENT_URL"
echo
echo "When recording starts, say: where are the pods"
echo

python3 boya_voice_server.py --test 5
