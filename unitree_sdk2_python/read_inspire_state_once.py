import time

from unitree_sdk2py.core.channel import ChannelFactoryInitialize, ChannelSubscriber
from unitree_sdk2py.idl.unitree_go.msg.dds_ import MotorStates_

latest_msg = None


def get_states(msg):
    try:
        return msg.states
    except Exception:
        return msg.states()


def get_q(s):
    try:
        return s.q
    except Exception:
        return s.q()


def get_lost(s):
    try:
        return s.lost
    except Exception:
        return s.lost()


def handler(msg: MotorStates_):
    global latest_msg
    latest_msg = msg


def print_msg(tag, msg):
    print(f"\n---- {tag} ----")
    states = get_states(msg)
    for i, s in enumerate(states):
        print(f"id={i:02d}, q={get_q(s)}, lost={get_lost(s)}")


def main():
    ChannelFactoryInitialize(0, "eth0")

    sub = ChannelSubscriber("rt/inspire/state", MotorStates_)
    sub.Init(handler, 10)

    print("Listening to rt/inspire/state for 4 seconds...")

    first = None
    start = time.time()

    while time.time() - start < 4.0:
        if latest_msg is not None and first is None:
            first = latest_msg
        time.sleep(0.1)

    last = latest_msg

    if first is None or last is None:
        print("No inspire state received.")
        return

    print_msg("first state", first)
    print_msg("last state", last)

    first_states = get_states(first)
    last_states = get_states(last)

    print("\n---- lost delta ----")
    for i in range(min(len(first_states), len(last_states))):
        delta = get_lost(last_states[i]) - get_lost(first_states[i])
        print(f"id={i:02d}, lost_delta={delta}")

    print("\nInterpretation:")
    print("0-5  = right hand")
    print("6-11 = left hand")
    print("If lost_delta keeps increasing, that hand is not communicating correctly.")


if __name__ == "__main__":
    main()
