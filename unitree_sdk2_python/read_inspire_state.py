import time

from unitree_sdk2py.core.channel import ChannelFactoryInitialize, ChannelSubscriber
from unitree_sdk2py.idl.unitree_go.msg.dds_ import MotorStates_


def handler(msg: MotorStates_):
    try:
        states = msg.states
    except Exception:
        states = msg.states()

    print("---- inspire hand state ----")
    for i, s in enumerate(states):
        try:
            q = s.q
        except Exception:
            q = s.q()

        try:
            lost = s.lost
        except Exception:
            lost = s.lost()

        print(f"id={i:02d}, q={q}, lost={lost}")
    print("----------------------------")


def main():
    ChannelFactoryInitialize(0, "eth0")

    sub = ChannelSubscriber("rt/inspire/state", MotorStates_)
    sub.Init(handler, 10)

    print("Listening to rt/inspire/state ...")
    print("Press Ctrl+C to stop.")

    while True:
        time.sleep(1)


if __name__ == "__main__":
    main()
