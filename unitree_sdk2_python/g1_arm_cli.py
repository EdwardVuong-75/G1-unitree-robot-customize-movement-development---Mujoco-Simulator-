import sys

from unitree_sdk2py.core.channel import ChannelFactoryInitialize
from unitree_sdk2py.g1.arm.g1_arm_action_client import G1ArmActionClient, action_map

def usage():
    print("Usage:")
    print("  python3 g1_arm_cli.py eth0 <gesture>")
    print("Available gestures:")
    for name in sorted(action_map.keys()):
        print(f"  {name}")


def main():
    if len(sys.argv) < 3:
        usage()
        return

    net = sys.argv[1]
    gesture = sys.argv[2]

    if gesture not in action_map:
        print(f"[Error] Unknown gesture: {gesture}")
        usage()
        return

    action_id = action_map[gesture]

    print("NetworkInterface:", net)
    print("Gesture:", gesture, "(action_id =", action_id, ")")

    ChannelFactoryInitialize(0, net)

    client = G1ArmActionClient()
    client.SetTimeout(10.0)
    client.Init()

    code = client.ExecuteAction(action_id)
    if code == 0:
        print(f"[Arm] '{gesture}' sent successfully.")
    else:
        print(f"[Arm] ExecuteAction failed, return code={code}")


if __name__ == "__main__":
    main()
