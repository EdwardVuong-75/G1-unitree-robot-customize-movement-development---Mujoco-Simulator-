"""G1 arm joint names (MuJoCo) <-> motor indices (rt/arm_sdk). Legs are never listed."""

from __future__ import annotations

# Motor 29 is arm_sdk blend weight, not a real joint.
ARM_SDK_WEIGHT_INDEX = 29

# Wrist pitch/yaw are invalid on 23-DoF (5-DoF arms).
JOINTS_5DOF = [
    ("left_shoulder_pitch_joint", 15),
    ("left_shoulder_roll_joint", 16),
    ("left_shoulder_yaw_joint", 17),
    ("left_elbow_joint", 18),
    ("left_wrist_roll_joint", 19),
    ("right_shoulder_pitch_joint", 22),
    ("right_shoulder_roll_joint", 23),
    ("right_shoulder_yaw_joint", 24),
    ("right_elbow_joint", 25),
    ("right_wrist_roll_joint", 26),
]

JOINTS_7DOF_EXTRA = [
    ("left_wrist_pitch_joint", 20),
    ("left_wrist_yaw_joint", 21),
    ("right_wrist_pitch_joint", 27),
    ("right_wrist_yaw_joint", 28),
]

WAIST_JOINTS = [
    ("waist_yaw_joint", 12),
]

# Conservative send limits (tighter than mechanical). Signs for roll/elbow
# follow Unitree's usual G1 convention; confirm on first live session.
SEND_LIMITS = {
    15: (-2.6, 2.6),
    16: (-0.2, 1.8),
    17: (-1.5, 1.5),
    18: (-0.4, 1.9),
    19: (-1.5, 1.5),
    20: (-1.4, 1.4),
    21: (-1.4, 1.4),
    22: (-2.6, 2.6),
    23: (-1.8, 0.2),
    24: (-1.5, 1.5),
    25: (-0.4, 1.9),
    26: (-1.5, 1.5),
    27: (-1.4, 1.4),
    28: (-1.4, 1.4),
    12: (-1.0, 1.0),
}

WRIST_EXTRA_INDICES = [20, 21, 27, 28]


def joints_for_dof(dof: int, include_waist: bool = False):
    pairs = list(JOINTS_5DOF)
    if dof == 7:
        pairs.extend(JOINTS_7DOF_EXTRA)
    if include_waist:
        pairs.extend(WAIST_JOINTS)
    return pairs
