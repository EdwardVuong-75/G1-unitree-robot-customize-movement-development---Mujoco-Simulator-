#!/usr/bin/env python3
"""Live MuJoCo upper-body puppet -> real G1 arms.

Preferred (WSL/Windows USB cannot see rt/lowstate): run the HTTP bridge
ON the robot, then on this PC:

    python3 live_arm_to_g1.py --bridge http://192.168.123.164:5012 --dof 5

Direct DDS (only if probe_g1_arm_dof.py actually receives lowstate):

    python3 live_arm_to_g1.py eth0 --dof 5

Keys (MuJoCo window must be focused):
    SPACE  arm/disarm sending to the real robot (starts DISARMED)
    M      toggle built-in wave demo vs slider posing
    close  quit (ramps arm_sdk weight to 0)

Pose with the "G1 arm sliders" window (full names). The Joint panel in
MuJoCo also sticks now (physics is not stepped over your edits).

Do not run this at the same time as g1_arm_cli.py, the welcome-app arm
buttons, or arm_mimic_server_right.py.

Legs are never commanded.
"""

from __future__ import annotations

import argparse
import json
import math
import sys
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

import numpy as np

SDK_ROOT = Path(__file__).resolve().parent.parent
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(SDK_ROOT))

from unitree_sdk2py.core.channel import ChannelFactoryInitialize, ChannelPublisher, ChannelSubscriber
from unitree_sdk2py.idl.default import unitree_hg_msg_dds__LowCmd_
from unitree_sdk2py.idl.unitree_hg.msg.dds_ import LowCmd_, LowState_
from unitree_sdk2py.utils.crc import CRC
from unitree_sdk2py.utils.thread import RecurrentThread

from joints import ARM_SDK_WEIGHT_INDEX, SEND_LIMITS, joints_for_dof

try:
    import mujoco
    import mujoco.viewer
except ImportError:
    print("Install MuJoCo on this computer:  pip install mujoco")
    sys.exit(1)

CONTROL_DT = 0.02
WEIGHT_RAMP_TIME = 3.0
MAX_STEP_PER_TICK = 0.015
KP = 25.0
KD = 1.0
MODEL_PATH = HERE / "models" / "g1_upper_body.xml"


class LiveArmBridge:
    def __init__(self, joint_pairs):
        self.joint_pairs = joint_pairs
        self.indices = [idx for _, idx in joint_pairs]
        self.low_cmd = unitree_hg_msg_dds__LowCmd_()
        self.low_state = None
        self.have_low_state = False
        self.crc = CRC()
        self.lock = threading.Lock()
        self.targets = {idx: 0.0 for idx in self.indices}
        self.commanded = {idx: 0.0 for idx in self.indices}
        self.enabled = False
        self.weight = 0.0
        self.last_target_time = time.time()
        self.publisher = ChannelPublisher("rt/arm_sdk", LowCmd_)
        self.publisher.Init()
        self.subscriber = ChannelSubscriber("rt/lowstate", LowState_)
        self.subscriber.Init(self._on_low_state, 10)
        self._thread = None

    def _on_low_state(self, msg):
        self.low_state = msg
        self.have_low_state = True

    def wait_state(self, timeout=8.0) -> bool:
        t0 = time.time()
        while not self.have_low_state:
            if time.time() - t0 > timeout:
                return False
            time.sleep(0.05)
        with self.lock:
            for idx in self.indices:
                q = float(self.low_state.motor_state[idx].q)
                self.targets[idx] = q
                self.commanded[idx] = q
            self.last_target_time = time.time()
        return True

    def start(self):
        self._thread = RecurrentThread(interval=CONTROL_DT, target=self._tick, name="mujoco_arm_sdk")
        self._thread.Start()

    def set_from_q(self, q_by_idx: dict[int, float]):
        with self.lock:
            for idx, val in q_by_idx.items():
                lo, hi = SEND_LIMITS.get(idx, (-1.5, 1.5))
                self.targets[idx] = float(np.clip(val, lo, hi))
            self.last_target_time = time.time()

    def _tick(self):
        if self.low_state is None:
            return
        stale = (time.time() - self.last_target_time) > 0.8
        want_on = self.enabled and not stale
        step = CONTROL_DT / WEIGHT_RAMP_TIME
        self.weight = min(1.0, self.weight + step) if want_on else max(0.0, self.weight - step)
        self.low_cmd.motor_cmd[ARM_SDK_WEIGHT_INDEX].q = self.weight

        with self.lock:
            snapshot = dict(self.targets)

        for idx in self.indices:
            current = self.commanded[idx]
            desired = snapshot[idx]
            delta = np.clip(desired - current, -MAX_STEP_PER_TICK, MAX_STEP_PER_TICK)
            self.commanded[idx] = current + delta
            cmd = self.low_cmd.motor_cmd[idx]
            cmd.q = self.commanded[idx]
            cmd.dq = 0.0
            cmd.tau = 0.0
            cmd.kp = KP
            cmd.kd = KD

        self.low_cmd.crc = self.crc.Crc(self.low_cmd)
        self.publisher.Write(self.low_cmd)

    def release_blocking(self):
        self.enabled = False
        t0 = time.time()
        while self.weight > 0.01 and time.time() - t0 < WEIGHT_RAMP_TIME + 1.0:
            time.sleep(0.05)


class HttpArmClient:
    """PC-side client for g1_arm_bridge_server.py running on the G1."""

    def __init__(self, base_url: str, joint_pairs):
        self.base = base_url.rstrip("/")
        self.indices = [idx for _, idx in joint_pairs]
        self.commanded = {idx: 0.0 for idx in self.indices}
        self._enabled = False
        self.weight = 0.0

    def _req(self, method: str, path: str, payload=None, timeout=2.0):
        data = None if payload is None else json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(
            self.base + path,
            data=data,
            method=method,
            headers={"Content-Type": "application/json"} if data else {},
        )
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8") or "{}")

    def wait_state(self, timeout=8.0) -> bool:
        t0 = time.time()
        while time.time() - t0 < timeout:
            try:
                body = self._req("GET", "/state")
            except KeyboardInterrupt:
                print("\nCancelled.")
                return False
            except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
                print(f"bridge: {exc}")
                time.sleep(0.25)
                continue
            if body.get("ok") and body.get("have_low_state"):
                q = body.get("q") or {}
                for idx in self.indices:
                    if str(idx) in q:
                        self.commanded[idx] = float(q[str(idx)])
                self.weight = float(body.get("weight") or 0.0)
                return True
            time.sleep(0.25)
        return False

    def start(self):
        return None

    def set_from_q(self, q_by_idx: dict[int, float]):
        clipped = {}
        for idx, val in q_by_idx.items():
            lo, hi = SEND_LIMITS.get(idx, (-1.5, 1.5))
            clipped[idx] = float(np.clip(val, lo, hi))
            self.commanded[idx] = clipped[idx]
        try:
            self._req("POST", "/targets", {"targets": {str(k): v for k, v in clipped.items()}})
        except (urllib.error.URLError, TimeoutError) as exc:
            print(f"bridge targets failed: {exc}")

    @property
    def enabled(self):
        return self._enabled

    @enabled.setter
    def enabled(self, value: bool):
        self._enabled = bool(value)
        path = "/enable" if self._enabled else "/disable"
        try:
            self._req("POST", path)
        except (urllib.error.URLError, TimeoutError) as exc:
            print(f"bridge enable/disable failed: {exc}")

    def release_blocking(self):
        self.enabled = False
        time.sleep(0.2)


def start_slider_window(pairs, get_q, pending: dict, lock: threading.Lock, ui: dict):
    """Named sliders. Do not write slider values back from the sim (that snaps the thumb)."""
    try:
        import tkinter as tk
    except ImportError:
        print("tkinter not available.")
        return None

    def run():
        root = tk.Tk()
        root.title("G1 arm sliders — Space in MuJoCo arms the robot")
        tk.Label(root, text="Drag these bars (not the MuJoCo Joint panel). Turn M off.").pack(anchor=tk.W, padx=8, pady=4)

        for name, idx in pairs:
            lo, hi = SEND_LIMITS.get(idx, (-1.5, 1.5))
            try:
                start = float(np.clip(get_q(name), lo, hi))
            except Exception:
                start = 0.0
            row = tk.Frame(root)
            row.pack(fill=tk.X, padx=8, pady=3)
            label = name.replace("_joint", "").replace("_", " ")
            tk.Label(row, text=label, width=26, anchor=tk.W).pack(side=tk.LEFT)
            val_lbl = tk.Label(row, text=f"{start:.2f}", width=7)
            val_lbl.pack(side=tk.RIGHT)

            def make_cmd(n, lab, low, high):
                def _cmd(raw):
                    if ui.get("demo") or ui.get("siuu"):
                        return
                    q = float(np.clip(float(raw), low, high))
                    lab.config(text=f"{q:.2f}")
                    with lock:
                        pending[n] = q
                return _cmd

            scale = tk.Scale(
                row,
                from_=lo,
                to=hi,
                resolution=0.01,
                orient=tk.HORIZONTAL,
                length=300,
                showvalue=0,
                command=make_cmd(name, val_lbl, lo, hi),
            )
            scale.set(start)
            scale.pack(side=tk.LEFT, fill=tk.X, expand=True)

        root.mainloop()

    t = threading.Thread(target=run, name="g1_sliders", daemon=True)
    t.start()
    return t


def demo_wave(t: float, name: str) -> float | None:
    s = math.sin(t * 1.2)
    c = math.cos(t * 1.2)
    table = {
        "left_shoulder_pitch_joint": 0.25 * s,
        "left_shoulder_roll_joint": 0.35 + 0.15 * c,
        "left_elbow_joint": 0.6 + 0.25 * s,
        "right_shoulder_pitch_joint": 0.25 * c,
        "right_shoulder_roll_joint": -0.35 - 0.15 * s,
        "right_elbow_joint": 0.6 + 0.25 * c,
    }
    return table.get(name)


# SIUU from user's slider screenshot (exact values, both arms).
SIUU_POSE = {
    "left_shoulder_pitch_joint": -1.80,
    "left_shoulder_roll_joint": 0.10,
    "left_shoulder_yaw_joint": -0.21,
    "left_elbow_joint": 0.01,
    "left_wrist_roll_joint": 0.16,
    "left_wrist_pitch_joint": -0.24,
    "left_wrist_yaw_joint": 0.00,
    "right_shoulder_pitch_joint": -1.92,
    "right_shoulder_roll_joint": -0.39,
    "right_shoulder_yaw_joint": 0.10,
    "right_elbow_joint": -0.01,
    "right_wrist_roll_joint": -0.06,
    "right_wrist_pitch_joint": -0.14,
    "right_wrist_yaw_joint": -0.03,
}
SIUU_BLEND_S = 0.5
SIUU_HOLD_S = 2.0
SIUU_SPREAD_S = 1.0

# Wide, slightly down/back (photo), not a T-pose. Roll stays inside SEND_LIMITS ±1.8.
SIUU_SPREAD_POSE = {
    "left_shoulder_pitch_joint": 0.25,
    "left_shoulder_roll_joint": 0.49,
    "left_shoulder_yaw_joint": 0.72,
    "left_elbow_joint": 0.77,
    "left_wrist_roll_joint": -1.11,
    "left_wrist_pitch_joint": 0.08,
    "left_wrist_yaw_joint": 0.03,
    "right_shoulder_pitch_joint": 0.25,
    "right_shoulder_roll_joint": -0.50,
    "right_shoulder_yaw_joint": -0.58,
    "right_elbow_joint": 0.54,
    "right_wrist_roll_joint": 1.50,
    "right_wrist_pitch_joint": 0.04,
    "right_wrist_yaw_joint": -0.05,
}


def _apply_joint_lerp(name_to_qadr, data, start: dict, target: dict, names, u: float):
    u = min(1.0, max(0.0, u))
    u = u * u * (3.0 - 2.0 * u)
    for name in names:
        if name not in target or name not in name_to_qadr:
            continue
        adr, idx = name_to_qadr[name]
        a = start.get(name, float(data.qpos[adr]))
        b = target[name]
        lo, hi = SEND_LIMITS.get(idx, (-1.5, 1.5))
        data.qpos[adr] = float(np.clip((1.0 - u) * a + u * b, lo, hi))


def _siuu_motion(name_to_qadr, data, start: dict, t0: float, now: float):
    """Left to SIUU, right crosses, hold, then both arms open wide."""
    elapsed = now - t0
    left = [n for n in SIUU_POSE if n.startswith("left_")]
    right = [n for n in SIUU_POSE if n.startswith("right_")]
    all_arms = left + right
    left_end = SIUU_BLEND_S
    right_end = 2.0 * SIUU_BLEND_S
    hold_end = right_end + SIUU_HOLD_S
    if elapsed < left_end:
        _apply_joint_lerp(name_to_qadr, data, start, SIUU_POSE, left, elapsed / SIUU_BLEND_S)
        _apply_joint_lerp(name_to_qadr, data, start, SIUU_POSE, right, 0.0)
    elif elapsed < right_end:
        _apply_joint_lerp(name_to_qadr, data, start, SIUU_POSE, left, 1.0)
        _apply_joint_lerp(
            name_to_qadr, data, start, SIUU_POSE, right, (elapsed - left_end) / SIUU_BLEND_S
        )
    elif elapsed < hold_end:
        _apply_joint_lerp(name_to_qadr, data, start, SIUU_POSE, left, 1.0)
        _apply_joint_lerp(name_to_qadr, data, start, SIUU_POSE, right, 1.0)
    else:
        _apply_joint_lerp(
            name_to_qadr,
            data,
            SIUU_POSE,
            SIUU_SPREAD_POSE,
            all_arms,
            (elapsed - hold_end) / SIUU_SPREAD_S,
        )


def parse_args():
    p = argparse.ArgumentParser(description="Live MuJoCo G1 arms over USB Ethernet")
    p.add_argument("iface", nargs="?", default=None, help="DDS iface on this PC (omit when using --bridge)")
    p.add_argument("--bridge", default=None, help="http://192.168.123.164:5012  (on-robot HTTP bridge)")
    p.add_argument("--dof", type=int, choices=(5, 7), default=5, help="5 = no wrist pitch/yaw, 7 = full wrists")
    p.add_argument("--waist", action="store_true", help="also command waist_yaw (off by default)")
    p.add_argument("--yes", action="store_true", help="skip Enter confirmation")
    return p.parse_args()


def main():
    args = parse_args()
    if not args.bridge and not args.iface:
        print("Use --bridge http://192.168.123.164:5012  (recommended) or pass a DDS interface.")
        sys.exit(1)
    pairs = joints_for_dof(args.dof, include_waist=args.waist)

    print("WARNING: Clear people and obstacles around BOTH arms.")
    print("Do not run g1_arm_cli / welcome arm buttons / arm_mimic_server at the same time.")
    if args.bridge:
        print(f"HTTP bridge: {args.bridge}   dof={args.dof}   waist={args.waist}")
    else:
        print(f"PC DDS interface: {args.iface}   dof={args.dof}   waist={args.waist}")
    print("SPACE = enable real robot    M = wave    S = SIUU (cross, then both arms spread)")
    print("Pose with the G1 arm sliders window. Clear space above the helmet.")
    if not args.yes:
        input("Press Enter to open MuJoCo...")

    if args.bridge:
        bridge = HttpArmClient(args.bridge, pairs)
        if not bridge.wait_state():
            print("No reply from the on-robot bridge. SSH to 192.168.123.164 and start g1_arm_bridge_server.py")
            sys.exit(2)
    else:
        ChannelFactoryInitialize(0, args.iface)
        bridge = LiveArmBridge(pairs)
        if not bridge.wait_state():
            print("No rt/lowstate from this PC. Use the on-robot bridge instead:")
            print("  python3 live_arm_to_g1.py --bridge http://192.168.123.164:5012 --dof", args.dof)
            sys.exit(2)
        bridge.start()

    model = mujoco.MjModel.from_xml_path(str(MODEL_PATH))
    data = mujoco.MjData(model)
    name_to_qadr = {}
    for name, idx in pairs:
        jid = mujoco.mj_name2id(model, mujoco.mjtObj.mjOBJ_JOINT, name)
        if jid < 0:
            print(f"Model missing joint {name}")
            sys.exit(3)
        name_to_qadr[name] = (model.jnt_qposadr[jid], idx)

    for name, (adr, idx) in name_to_qadr.items():
        data.qpos[adr] = bridge.commanded[idx]
    mujoco.mj_forward(model, data)
    print("Seeded sim from robot /state:",
          {name: round(float(data.qpos[adr]), 3) for name, (adr, idx) in name_to_qadr.items()})

    ui = {"enable": False, "demo": False, "siuu": False, "t0": time.time(), "blend_from": {}}
    pending_sliders: dict[str, float] = {}
    slider_lock = threading.Lock()

    def current_q(name: str) -> float:
        adr, _idx = name_to_qadr[name]
        return float(data.qpos[adr])

    start_slider_window(pairs, current_q, pending_sliders, slider_lock, ui)

    def key_callback(keycode):
        try:
            ch = chr(keycode)
        except ValueError:
            return
        if ch == " ":
            ui["enable"] = not ui["enable"]
            bridge.enabled = ui["enable"]
            print(f"REAL ROBOT {'ARMED' if ui['enable'] else 'DISARMED'}  weight→{bridge.weight:.2f}")
        elif ch in ("m", "M"):
            ui["demo"] = not ui["demo"]
            ui["siuu"] = False
            ui["t0"] = time.time()
            print(f"Demo wave {'ON' if ui['demo'] else 'OFF (use sliders)'}")
        elif ch in ("s", "S"):
            ui["siuu"] = not ui["siuu"]
            ui["demo"] = False
            ui["t0"] = time.time()
            ui["blend_from"] = {n: float(data.qpos[adr]) for n, (adr, _i) in name_to_qadr.items()}
            print(f"SIUU {'ON (cross, then both arms spread)' if ui['siuu'] else 'OFF'} — keep clear of the head")

    print("Viewer running. SPACE to arm the real robot (starts disarmed).")
    print("S = SIUU (left, right cross, hold, then both arms spread). M = small wave. Sliders when both are off.")
    try:
        with mujoco.viewer.launch_passive(model, data, key_callback=key_callback) as viewer:
            while viewer.is_running():
                step_start = time.time()
                if ui["siuu"]:
                    _siuu_motion(name_to_qadr, data, ui["blend_from"], ui["t0"], time.time())
                elif ui["demo"]:
                    t = time.time() - ui["t0"]
                    for name, (adr, idx) in name_to_qadr.items():
                        v = demo_wave(t, name)
                        if v is not None:
                            lo, hi = SEND_LIMITS.get(idx, (-1.5, 1.5))
                            data.qpos[adr] = float(np.clip(v, lo, hi))
                else:
                    with slider_lock:
                        updates = dict(pending_sliders)
                        pending_sliders.clear()
                    for name, val in updates.items():
                        if name not in name_to_qadr:
                            continue
                        adr, idx = name_to_qadr[name]
                        lo, hi = SEND_LIMITS.get(idx, (-1.5, 1.5))
                        data.qpos[adr] = float(np.clip(val, lo, hi))
                mujoco.mj_forward(model, data)

                q_by_idx = {idx: float(data.qpos[adr]) for name, (adr, idx) in name_to_qadr.items()}
                bridge.set_from_q(q_by_idx)
                viewer.sync()
                leftover = CONTROL_DT - (time.time() - step_start)
                if leftover > 0:
                    time.sleep(leftover)
    finally:
        print("Releasing arm_sdk...")
        bridge.release_blocking()
        print("Done.")


if __name__ == "__main__":
    main()
