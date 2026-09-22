#!/usr/bin/env bash
set -e

cd "$(dirname "$0")"

export NETWORK_INTERFACE="${NETWORK_INTERFACE:-eth0}"
export DEFAULT_CAMERA_ID="${DEFAULT_CAMERA_ID:-3}"
export PORT="${PORT:-5010}"

# 这里不修改旧项目，只调用旧项目中已能使用的底层脚本
export G1_WALK_CLI="${G1_WALK_CLI:-/home/unitree/unitree_sdk2_python/g1_walk_cli.py}"
#Get Arm movements
export G1_ARM_CLI="${G1_ARM_CLI:-/home/unitree/unitree_sdk2_python/g1_arm_cli.py}"

export MJHOME_SAY_SCRIPT="${MJHOME_SAY_SCRIPT:-/home/unitree/unitree_sdk2_python/mjhome_say2.py}"

export G1_TTS_CLI="${G1_TTS_CLI:-/home/unitree/unitree_sdk2_python/mjhome_ttss.py}"

export VOSK_MODEL_PATH=/home/unitree/g1_voice_model/vosk-model-small-en-us-0.15

# 如果默认音频设备没声音，可以手动设置：
# export AUDIO_DEVICE=plughw:1,0

python3 app.py
