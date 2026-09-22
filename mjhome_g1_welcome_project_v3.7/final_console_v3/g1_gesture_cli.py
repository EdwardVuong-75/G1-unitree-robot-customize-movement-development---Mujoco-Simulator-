#!/usr/bin/env python3
import sys
import time
from pathlib import Path

SDK_PATHS = [
    "/home/unitree/unitree_sdk2_python",
    "/home/unitree/unitree_sdk2_python/src",
    "/home/unitree/unitree_sdk2_latest",
    "/home/unitree/unitree_sdk2-main",
]

for p in SDK_PATHS:
    if Path(p).exists() and p not in sys.path:
        sys.path.insert(0, p)

ACTION_FALLBACK = {
    "release_arm": 99,
    "release arm": 99,
    "face_wave": 25,
    "face wave": 25,
    "wave": 25,
    "handshake": 27,
    "shake_hand": 27,
    "shake hand": 27,
}

try:
    from unitree_sdk2py.core.channel import ChannelFactoryInitialize
    from unitree_sdk2py.g1.arm.g1_arm_action_client import G1ArmActionClient
    try:
        from unitree_sdk2py.g1.arm.g1_arm_action_client import action_map as SDK_ACTION_MAP
    except Exception:
        SDK_ACTION_MAP = {}
except Exception as e:
    print("IMPORT_ERROR:", repr(e))
    sys.exit(2)


def resolve_action_id(name: str):
    raw = name.strip().lower()
    candidates = [raw, raw.replace("_", " "), raw.replace("-", " ")]
    for key in candidates:
        if key in ACTION_FALLBACK:
            return ACTION_FALLBACK[key]
    for key in candidates:
        if SDK_ACTION_MAP and key in SDK_ACTION_MAP:
            return SDK_ACTION_MAP[key]
    return None


def list_actions():
    print("Fallback actions:")
    for k, v in sorted(ACTION_FALLBACK.items()):
        print(f"  {k}: {v}")
    if SDK_ACTION_MAP:
        print("\nSDK action_map:")
        for k, v in sorted(SDK_ACTION_MAP.items(), key=lambda x: str(x[0])):
            print(f"  {k}: {v}")


def main():
    if len(sys.argv) < 3:
        print("Usage: python3 g1_gesture_cli.py <net_if> <shake_hand|face_wave|release_arm|list>")
        sys.exit(1)
    net_if = sys.argv[1]
    action_name = sys.argv[2].strip().lower()
    if action_name == "list":
        list_actions()
        sys.exit(0)

    action_id = resolve_action_id(action_name)
    if action_id is None:
        print(f"Unknown action: {action_name}")
        list_actions()
        sys.exit(1)

    print(f"Network interface: {net_if}")
    print(f"Action: {action_name}")
    print(f"Action ID: {action_id}")

    ChannelFactoryInitialize(0, net_if)
    client = G1ArmActionClient()
    client.SetTimeout(10.0)
    client.Init()

    result = client.ExecuteAction(action_id)
    print("ExecuteAction result:", result)

    if action_name not in {"release_arm", "release arm"}:
        time.sleep(3.0)
        release_id = resolve_action_id("release_arm")
        if release_id is not None:
            release_result = client.ExecuteAction(release_id)
            print("Release arm result:", release_result)


if __name__ == "__main__":
    main()
