#!/usr/bin/env python3
"""Run ON the G1. Owns rt/arm_sdk DDS locally; exposes HTTP for the PC MuJoCo client.

USB Ethernet from a PC can ping 192.168.123.164 (this computer) but usually
cannot see rt/lowstate on the motion board. This process talks DDS on the
robot's eth0 (where it already works) and accepts joint targets over TCP.

    python3 g1_arm_bridge_server.py eth0 --dof 5

Do NOT run this together with g1_arm_cli.py, welcome-app arm buttons, or
arm_mimic_server_right.py. Legs are never commanded.

Uses only the stdlib HTTP server so nothing extra needs pip on the robot.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import numpy as np

SDK_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(SDK_ROOT))

from unitree_sdk2py.core.channel import ChannelFactoryInitialize, ChannelPublisher, ChannelSubscriber
from unitree_sdk2py.idl.default import unitree_hg_msg_dds__LowCmd_
from unitree_sdk2py.idl.unitree_hg.msg.dds_ import LowCmd_, LowState_
from unitree_sdk2py.utils.crc import CRC
from unitree_sdk2py.utils.thread import RecurrentThread

from joints import ARM_SDK_WEIGHT_INDEX, SEND_LIMITS, joints_for_dof

CONTROL_DT = 0.02
WEIGHT_RAMP_TIME = 3.0
MAX_STEP_PER_TICK = 0.05 #0.015 before
STALE_TIMEOUT = 0.8
KP = 25.0
KD = 1.0
DEFAULT_PORT = 5012


class ArmSdkController:
    def __init__(self, joint_pairs):
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

    def start(self, timeout=8.0) -> bool:
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
        self._thread = RecurrentThread(interval=CONTROL_DT, target=self._tick, name="g1_arm_bridge")
        self._thread.Start()
        return True

    def set_targets(self, angle_by_joint):
        with self.lock:
            for j_str, val in angle_by_joint.items():
                idx = int(j_str)
                if idx not in self.targets:
                    continue
                lo, hi = SEND_LIMITS.get(idx, (-1.5, 1.5))
                self.targets[idx] = float(np.clip(val, lo, hi))
            self.last_target_time = time.time()

    def snapshot_q(self):
        with self.lock:
            return {str(idx): self.commanded[idx] for idx in self.indices}

    def measured_q(self):
        if self.low_state is None:
            return {}
        return {str(idx): float(self.low_state.motor_state[idx].q) for idx in self.indices}

    def _tick(self):
        if self.low_state is None:
            return
        stale = (time.time() - self.last_target_time) > STALE_TIMEOUT
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


def make_handler(controller: ArmSdkController):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, fmt, *args):
            return

        def _json(self, code, payload):
            body = json.dumps(payload).encode("utf-8")
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _read_json(self):
            n = int(self.headers.get("Content-Length", "0") or 0)
            raw = self.rfile.read(n) if n else b"{}"
            try:
                return json.loads(raw.decode("utf-8") or "{}")
            except json.JSONDecodeError:
                return {}

        def do_GET(self):
            if self.path in ("/health", "/state"):
                self._json(200, {
                    "ok": True,
                    "enabled": controller.enabled,
                    "weight": controller.weight,
                    "have_low_state": controller.have_low_state,
                    "q": controller.measured_q() or controller.snapshot_q(),
                })
                return
            self._json(404, {"ok": False, "error": "not found"})

        def do_POST(self):
            if self.path == "/enable":
                controller.enabled = True
                self._json(200, {"ok": True, "enabled": True})
                return
            if self.path == "/disable":
                controller.enabled = False
                self._json(200, {"ok": True, "enabled": False})
                return
            if self.path == "/targets":
                data = self._read_json()
                controller.set_targets(data.get("targets", {}))
                self._json(200, {"ok": True})
                return
            self._json(404, {"ok": False, "error": "not found"})

    return Handler


def _list_ifaces():
    names = []
    try:
        import subprocess
        res = subprocess.run(["ip", "-br", "addr"], capture_output=True, text=True, timeout=8)
        for line in res.stdout.splitlines():
            parts = line.split()
            if not parts:
                continue
            name = parts[0].split("@")[0]
            if name in ("lo", "loopback0") or name.startswith("docker"):
                continue
            names.append(name)
    except Exception:
        pass
    return names or ["eth0", "eth1"]


def _ping(host: str) -> bool:
    import subprocess
    try:
        return subprocess.run(
            ["ping", "-c", "1", "-W", "1", host],
            capture_output=True, timeout=5,
        ).returncode == 0
    except Exception:
        return False


def parse_args():
    p = argparse.ArgumentParser(description="G1 on-robot arm_sdk HTTP bridge")
    p.add_argument("iface", nargs="?", default="eth0", help="DDS interface ON THE ROBOT (usually eth0)")
    p.add_argument("--dof", type=int, choices=(5, 7), default=5)
    p.add_argument("--waist", action="store_true")
    p.add_argument("--host", default="0.0.0.0", help="bind address; 0.0.0.0 so the PC can reach it")
    p.add_argument("--port", type=int, default=DEFAULT_PORT)
    p.add_argument("--yes", action="store_true")
    p.add_argument("--timeout", type=float, default=15.0, help="seconds to wait for rt/lowstate")
    return p.parse_args()


def main():
    args = parse_args()
    pairs = joints_for_dof(args.dof, include_waist=args.waist)
    print("WARNING: Clear people and obstacles around BOTH arms.")
    print("Do not run g1_arm_cli / welcome arm buttons / arm_mimic_server at the same time.")
    print(f"Robot DDS iface={args.iface}  dof={args.dof}  listen={args.host}:{args.port}")
    print(f"Motion board ping 192.168.123.161: {'OK' if _ping('192.168.123.161') else 'FAIL (DDS will not work)'}")
    print("Other NICs:", ", ".join(_list_ifaces()))
    if not args.yes and not os.environ.get("ARM_BRIDGE_AUTOCONFIRM"):
        input("Press Enter to start the on-robot arm bridge...")

    ChannelFactoryInitialize(0, args.iface)
    controller = ArmSdkController(pairs)
    if not controller.start(timeout=args.timeout):
        print("No rt/lowstate on this interface.")
        print("On the G1, run:")
        print("  ip -br addr")
        print("  ping -c 2 192.168.123.161")
        print("  python3 check_dds_link.py")
        print("Then retry this server with the iface check_dds_link.py reports, e.g.:")
        print("  python3 g1_arm_bridge_server.py eth1 --dof 5")
        sys.exit(2)
    print("rt/lowstate OK. HTTP bridge ready.")

    server = ThreadingHTTPServer((args.host, args.port), make_handler(controller))
    print(f"PC should run: python3 live_arm_to_g1.py --bridge http://192.168.123.164:{args.port} --dof {args.dof}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopping...")
    finally:
        controller.enabled = False
        time.sleep(WEIGHT_RAMP_TIME)
        server.server_close()


if __name__ == "__main__":
    main()
