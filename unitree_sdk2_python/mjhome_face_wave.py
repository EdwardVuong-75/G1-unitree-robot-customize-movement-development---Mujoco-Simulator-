import sys
import time

from unitree_sdk2py.core.channel import ChannelFactoryInitialize
from unitree_sdk2py.g1.arm.g1_arm_action_client import G1ArmActionClient
from unitree_sdk2py.g1.arm.g1_arm_action_client import action_map


def main():
    if len(sys.argv) < 2:
        print("Usage: python3 mjhome_face_wave.py eth0")
        return

    network_interface = sys.argv[1]

    print("[MJHome] Initializing G1ArmActionClient...")
    ChannelFactoryInitialize(0, network_interface)

    client = G1ArmActionClient()
    client.SetTimeout(10.0)
    client.Init()

    print("[MJHome] Execute face wave...")
    client.ExecuteAction(action_map.get("face wave"))

    time.sleep(3)

    print("[MJHome] Release arm...")
    client.ExecuteAction(action_map.get("release arm"))

    print("[MJHome] Done.")


if __name__ == "__main__":
    main()
