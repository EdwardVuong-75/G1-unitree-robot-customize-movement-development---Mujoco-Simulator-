import sys
import time

from unitree_sdk2py.core.channel import ChannelFactoryInitialize
from unitree_sdk2py.g1.loco.g1_loco_client import LocoClient


DEFAULT_SPEED = 0.15
DEFAULT_TURN_SPEED = 0.20
DEFAULT_DURATION = 1.0
STEP_TIME = 0.05


def usage():
    print("Usage:")
    print("  python3 g1_walk_cli.py eth0 [stop|forward|back|left|right|turnleft|turnright] [speed] [duration]")
    print("Examples:")
    print("  python3 g1_walk_cli.py eth0 stop")
    print("  python3 g1_walk_cli.py eth0 forward")
    print("  python3 g1_walk_cli.py eth0 forward 0.15 1.0")
    print("  python3 g1_walk_cli.py eth0 turnleft 0.20 1.0")


def send_stop(client, duration=0.8):
    steps = max(1, int(duration / STEP_TIME))

    for i in range(steps):
        client.Move(0.0, 0.0, 0.0, False)
        time.sleep(STEP_TIME)

    client.StopMove()
    print("[Robot] Stop command sent.")


def send_move(client, vx, vy, vyaw, duration):
    steps = max(1, int(duration / STEP_TIME))

    print(f"[Robot] Move start: vx={vx}, vy={vy}, vyaw={vyaw}, duration={duration}s")

    for i in range(steps):
        client.Move(vx, vy, vyaw, False)
        print(f"Move {i+1}/{steps}: vx={vx}, vy={vy}, vyaw={vyaw}")
        time.sleep(STEP_TIME)

    print("[Robot] Auto stopping...")
    send_stop(client, duration=0.8)
    print("[Robot] Done.")


def main():
    if len(sys.argv) < 3:
        usage()
        return

    net = sys.argv[1]
    cmd = sys.argv[2].lower()

    speed = float(sys.argv[3]) if len(sys.argv) >= 4 else DEFAULT_SPEED
    duration = float(sys.argv[4]) if len(sys.argv) >= 5 else DEFAULT_DURATION

    print("NetworkInterface:", net)
    print("Command:", cmd)
    print("Speed:", speed)
    print("Duration:", duration)

    ChannelFactoryInitialize(0, net)

    client = LocoClient()
    client.SetTimeout(10.0)
    client.Init()

    vx = 0.0
    vy = 0.0
    vyaw = 0.0

    if cmd == "stop":
        print("[Command] Stop")
        send_stop(client)
        return

    elif cmd == "forward":
        print("[Command] Forward")
        vx = speed

    elif cmd == "back":
        print("[Command] Back")
        vx = -speed

    elif cmd == "left":
        print("[Command] Move left")
        vy = speed

    elif cmd == "right":
        print("[Command] Move right")
        vy = -speed

    elif cmd == "turnleft":
        print("[Command] Turn left")
        vyaw = float(sys.argv[3]) if len(sys.argv) >= 4 else DEFAULT_TURN_SPEED

    elif cmd == "turnright":
        print("[Command] Turn right")
        vyaw = -(float(sys.argv[3]) if len(sys.argv) >= 4 else DEFAULT_TURN_SPEED)

    else:
        print("[Error] Unknown command:", cmd)
        usage()
        return

    send_move(client, vx, vy, vyaw, duration)


if __name__ == "__main__":
    main()
