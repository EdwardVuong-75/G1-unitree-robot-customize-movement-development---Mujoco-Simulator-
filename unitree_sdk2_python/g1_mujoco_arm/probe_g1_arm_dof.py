#!/usr/bin/env python3
"""Read rt/lowstate from the Linux PC over USB Ethernet and guess 5-DoF vs 7-DoF arms.

Usage (on your computer, USB plugged into the G1):
    python3 probe_g1_arm_dof.py enxXXXXXXXX
    python3 probe_g1_arm_dof.py usb0

Do NOT pass eth0 unless that is the USB NIC *on this PC*. eth0 is usually
the name on the robot, not on the laptop.
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

SDK_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(SDK_ROOT))

from unitree_sdk2py.core.channel import ChannelFactoryInitialize, ChannelSubscriber
from unitree_sdk2py.idl.unitree_hg.msg.dds_ import LowState_

from joints import JOINTS_5DOF, JOINTS_7DOF_EXTRA, WRIST_EXTRA_INDICES

_state = {"msg": None}


def _on_state(msg: LowState_):
    _state["msg"] = msg


def _present(ms) -> bool:
    temps = list(ms.temperature) if ms.temperature is not None else [0, 0]
    return ms.mode != 0 or ms.vol > 1.0 or any(abs(t) > 0 for t in temps)


def main():
    if len(sys.argv) < 2:
        print("Usage: python3 probe_g1_arm_dof.py <usb_network_interface>")
        print("Example: python3 probe_g1_arm_dof.py enx00e04c360123")
        sys.exit(1)

    iface = sys.argv[1]
    print(f"DDS interface (this PC): {iface}")
    print("Waiting for rt/lowstate (up to 8s)...")
    ChannelFactoryInitialize(0, iface)
    sub = ChannelSubscriber("rt/lowstate", LowState_)
    sub.Init(_on_state, 10)

    t0 = time.time()
    while _state["msg"] is None and time.time() - t0 < 8.0:
        time.sleep(0.05)

    msg = _state["msg"]
    if msg is None:
        print("No rt/lowstate. Check USB Ethernet IP (often 192.168.123.x) and the interface name.")
        sys.exit(2)

    print(f"mode_machine={getattr(msg, 'mode_machine', '?')}")
    print(f"{'joint':32} {'idx':>3} {'q':>8} {'dq':>8} {'mode':>5} {'vol':>6} present")
    wrist_ok = 0
    for name, idx in JOINTS_5DOF + JOINTS_7DOF_EXTRA:
        ms = msg.motor_state[idx]
        ok = _present(ms)
        if idx in WRIST_EXTRA_INDICES and ok:
            wrist_ok += 1
        print(f"{name:32} {idx:3d} {ms.q:8.3f} {ms.dq:8.3f} {ms.mode:5d} {ms.vol:6.1f} {ok}")

    dof = 7 if wrist_ok >= 2 else 5
    print()
    print(f"Guess: {dof}-DoF arms (wrist pitch/yaw motors look {'present' if dof == 7 else 'absent'}).")
    print(f"Use: python3 live_arm_to_g1.py {iface} --dof {dof}")


if __name__ == "__main__":
    main()
