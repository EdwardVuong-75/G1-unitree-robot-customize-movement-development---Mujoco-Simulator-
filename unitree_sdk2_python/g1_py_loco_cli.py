import sys
import time

from unitree_sdk2py.core.channel import ChannelFactoryInitialize
from unitree_sdk2py.g1.loco.g1_loco_client import LocoClient


def usage():
    print("Usage:")
    print("  python3 g1_py_loco_cli.py <network_interface> [stop|forward|back|left|right]")
    print("Example:")
    print("  python3 g1_py_loco_cli.py eth0 stop")
    print("  python3 g1_py_loco_cli.py eth0 forward")


def main():
    if len(sys.argv) < 3:
        usage()
        return

    network_interface = sys.argv[1]
    cmd = sys.argv[2]

    print(f"NetworkInterface: {network_interface}")
    print(f"Command: {cmd}")

    ChannelFactoryInitialize(0, network_interface)

    client = LocoClient()
    client.SetTimeout(10.0)
    client.Init()

    vx = 0.0
    vy = 0.0
    vyaw = 0.0

    if cmd == "stop":
        print("[Command] StopMove")
        ret = client.StopMove()
        print("StopMove ret:", ret)
        return

    elif cmd == "forward":
        vx = 0.05
        print("[Command] Small forward")

    elif cmd == "back":
        vx = -0.05
        print("[Command] Small backward")

    elif cmd == "left":
        vyaw = 0.10
        print("[Command] Small turn left")

    elif cmd == "right":
        vyaw = -0.10
        print("[Command] Small turn right")

    else:
        print("Unknown command:", cmd)
        usage()
        return

    # Send small motion command for 0.5 seconds.
    for i in range(10):
        ret = client.Move(vx, vy, vyaw, False)
        print(f"Move ret: {ret}")
        time.sleep(0.05)

    # Always stop for safety.
    for i in range(10):
        ret = client.Move(0.0, 0.0, 0.0, False)
        print(f"Stop command ret: {ret}")
        time.sleep(0.05)

    ret = client.StopMove()
    print("Final StopMove ret:", ret)
    print("Done.")


if __name__ == "__main__":
    main()
