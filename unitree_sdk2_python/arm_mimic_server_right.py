"""
Standalone process: owns the persistent rt/arm_sdk DDS connection for
RIGHT ARM ONLY, exposing a tiny local HTTP API for app.py to push joint
targets to.

Run separately from app.py:
    python3 arm_mimic_server_right.py eth0

Do NOT run this at the same time as anything using G1ArmActionClient
(g1_arm_cli.py / arm_command() in app.py) for the right arm -- both
would fight over the same joints.

SCOPE: only RightShoulderPitch, RightShoulderRoll, RightElbow are in
CONTROLLED_JOINTS. No other joint index is ever written to arm_sdk, so
the left arm, waist, legs, etc. are never touched by this process and
stay under the robot's normal control.

SAFETY (carried over from the incident writeup -- read before running):
  - MAX_STEP_PER_TICK hard-limits how fast the COMMANDED POSITION can
    move per control tick, independent of how large a jump app.py asks
    for. This is the actual fix for arm "snapping" -- Unitree's own
    reference implementation clips targets the same way.
  - kp/kd and WEIGHT_RAMP_TIME are intentionally soft. Do not raise
    until you've confirmed calm, predictable behavior.
  - Auto-releases (ramps weight to 0) if no fresh target for
    STALE_TIMEOUT seconds.
  - JOINT_LIMITS are deliberately tighter than mechanical limits.
  - Set ARM_MIMIC_AUTOCONFIRM=1 to skip the interactive prompt (used by
    start.sh for non-interactive launch).
"""

import os
import sys
import threading
import time

import numpy as np
from flask import Flask, jsonify, request

from unitree_sdk2py.core.channel import ChannelPublisher, ChannelSubscriber, ChannelFactoryInitialize
from unitree_sdk2py.idl.default import unitree_hg_msg_dds__LowCmd_
from unitree_sdk2py.idl.unitree_hg.msg.dds_ import LowCmd_, LowState_
from unitree_sdk2py.utils.crc import CRC
from unitree_sdk2py.utils.thread import RecurrentThread


class G1JointIndex:
    RightShoulderPitch = 22
    RightShoulderRoll = 23
    RightElbow = 25
    RightWristPitch = 26
    kNotUsedJoint = 29  # arm_sdk enable weight


# Right arm ONLY. Nothing else is ever in this list, so nothing else is
# ever written by this process.
CONTROLLED_JOINTS = [
    G1JointIndex.RightShoulderPitch,
    G1JointIndex.RightShoulderRoll,
    G1JointIndex.RightElbow,
    G1JointIndex.RightWristPitch
]

# Conservative -- deliberately tighter than mechanical limits. Verify
# signs empirically (single joint at a time, arm clear of obstacles)
# before trusting/widening these.
JOINT_LIMITS = {
    G1JointIndex.RightShoulderPitch: (-1.2, 1.2),
    G1JointIndex.RightShoulderRoll:  (-1.6, 0.0),
    G1JointIndex.RightElbow:         (0.0, 1.8),
    G1JointIndex.RightWristPitch: (-1.6, 0.0),
}

STALE_TIMEOUT = 0.6
WEIGHT_RAMP_TIME = 3.0
CONTROL_DT = 0.02

# HARD SAFETY LIMIT -- see module docstring. 0.015 rad/tick @ 50Hz =
# 0.75 rad/s max joint speed (~43 deg/s). Raise only after confirming
# calm behavior at this rate.
MAX_STEP_PER_TICK = 0.015


class ArmMimicController:
    def __init__(self):
        self.low_cmd = unitree_hg_msg_dds__LowCmd_()
        self.low_state = None
        self.have_low_state = False
        self.crc = CRC()

        self.lock = threading.Lock()
        self.targets = {j: 0.0 for j in CONTROLLED_JOINTS}
        self.commanded_pos = {j: 0.0 for j in CONTROLLED_JOINTS}
        self.last_target_time = 0.0

        self.enabled = False
        self.weight = 0.0
        self.kp = 25.0
        self.kd = 1.0

        self.publisher = ChannelPublisher("rt/arm_sdk", LowCmd_)
        self.publisher.Init()
        self.subscriber = ChannelSubscriber("rt/lowstate", LowState_)
        self.subscriber.Init(self._on_low_state, 10)
        self._thread = None

    def _on_low_state(self, msg):
        self.low_state = msg
        if not self.have_low_state:
            self.have_low_state = True

    def start(self):
        t0 = time.time()
        while not self.have_low_state:
            if time.time() - t0 > 5.0:
                print("ArmMimic(right): no rt/lowstate after 5s, aborting")
                return False
            time.sleep(0.05)

        with self.lock:
            for j in CONTROLLED_JOINTS:
                q = self.low_state.motor_state[j].q
                self.targets[j] = q
                self.commanded_pos[j] = q
            self.last_target_time = time.time()

        self._thread = RecurrentThread(interval=CONTROL_DT, target=self._tick, name="arm_mimic_right")
        self._thread.Start()
        print("ArmMimic(right): control loop started")
        return True

    def set_targets(self, angle_by_joint):
        clamped = {}
        for j_str, val in angle_by_joint.items():
            j = int(j_str)
            if j not in CONTROLLED_JOINTS:
                continue  # ignore anything outside right-arm scope
            lo, hi = JOINT_LIMITS.get(j, (val, val))
            clamped[j] = float(np.clip(val, lo, hi))
        with self.lock:
            self.targets.update(clamped)
            self.last_target_time = time.time()

    def enable(self):
        self.enabled = True

    def disable(self):
        self.enabled = False

    def _tick(self):
        if self.low_state is None:
            return

        stale = (time.time() - self.last_target_time) > STALE_TIMEOUT
        want_on = self.enabled and not stale

        step = CONTROL_DT / WEIGHT_RAMP_TIME
        self.weight = min(1.0, self.weight + step) if want_on else max(0.0, self.weight - step)
        self.low_cmd.motor_cmd[G1JointIndex.kNotUsedJoint].q = self.weight

        with self.lock:
            snapshot = dict(self.targets)

        for j in CONTROLLED_JOINTS:
            current = self.commanded_pos[j]
            desired = snapshot[j]
            delta = np.clip(desired - current, -MAX_STEP_PER_TICK, MAX_STEP_PER_TICK)
            self.commanded_pos[j] = current + delta

            cmd = self.low_cmd.motor_cmd[j]
            cmd.q = self.commanded_pos[j]
            cmd.dq = 0.0
            cmd.tau = 0.0
            cmd.kp = self.kp
            cmd.kd = self.kd

        self.low_cmd.crc = self.crc.Crc(self.low_cmd)
        self.publisher.Write(self.low_cmd)


app = Flask(__name__)
controller = None


@app.route("/enable", methods=["POST"])
def api_enable():
    controller.enable()
    return jsonify({"ok": True})


@app.route("/disable", methods=["POST"])
def api_disable():
    controller.disable()
    return jsonify({"ok": True})


@app.route("/targets", methods=["POST"])
def api_targets():
    data = request.get_json(silent=True) or {}
    controller.set_targets(data.get("targets", {}))
    return jsonify({"ok": True})


@app.route("/health", methods=["GET"])
def api_health():
    return jsonify({"ok": True, "weight": controller.weight, "enabled": controller.enabled})


if __name__ == "__main__":
    net = sys.argv[1] if len(sys.argv) > 1 else "eth0"
    print("WARNING: Please ensure there are no obstacles/people close around the robot's right arm.")
    if not os.environ.get("ARM_MIMIC_AUTOCONFIRM"):
        input("Press Enter to continue...")

    ChannelFactoryInitialize(0, net)
    controller = ArmMimicController()
    if not controller.start():
        sys.exit(1)

    app.run(host="127.0.0.1", port=5011, debug=False, threaded=True)
