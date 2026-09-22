#!/usr/bin/env python3
"""Diagnose the USB-Ethernet -> DDS link to the G1 and find the right interface.

Read-only: this only subscribes to rt/lowstate. Nothing is ever published to
the robot.

Run on your PC (Linux, WSL2, or Windows), with the USB-C debug cable plugged in:

    python3 check_dds_link.py

It lists local interfaces, says which one holds a 192.168.123.x address, then
pings the right target:
  - on the G1 development PC (this machine is 192.168.123.164): the motion
    board at 192.168.123.161 (not itself)
  - on a laptop/WSL: 192.168.123.164 (SSH) and 192.168.123.161 (motion)

Then it tries each candidate interface with a real DDS subscription and
tells you which one actually receives lowstate.

unitree_sdk2py's ChannelFactory is a process-wide singleton that cannot be
re-initialized, so each interface is tested in its own subprocess via the
`--try-iface` mode below.
"""

from __future__ import annotations

import argparse
import ipaddress
import os
import platform
import re
import socket
import subprocess
import sys
import time
from pathlib import Path

SDK_ROOT = Path(__file__).resolve().parent.parent
HERE = Path(__file__).resolve().parent

DEV_PC_IP = "192.168.123.164"
MOTION_BOARD_IP = "192.168.123.161"
ROBOT_IP = DEV_PC_IP
ROBOT_NET = ipaddress.ip_network("192.168.123.0/24")
WSL_NAT_NETS = [ipaddress.ip_network(f"172.{n}.0.0/16") for n in range(16, 32)]

EXIT_GOT_LOWSTATE = 0
EXIT_NO_LOWSTATE = 3
EXIT_INIT_FAILED = 4


# --------------------------------------------------------------------------
# host / interface discovery
# --------------------------------------------------------------------------

def is_windows() -> bool:
    return os.name == "nt"


def is_wsl() -> bool:
    if is_windows():
        return False
    if "microsoft" in platform.release().lower():
        return True
    try:
        return "microsoft" in Path("/proc/version").read_text().lower()
    except OSError:
        return False


def _ifaces_from_psutil() -> dict[str, list[str]] | None:
    try:
        import psutil  # optional
    except ImportError:
        return None
    out: dict[str, list[str]] = {}
    for name, addrs in psutil.net_if_addrs().items():
        out[name] = [a.address for a in addrs if a.family == socket.AF_INET and a.address]
    return out


def _ifaces_from_ip_command() -> dict[str, list[str]]:
    """Parse `ip -br addr` (Linux/WSL fallback when psutil is missing)."""
    out: dict[str, list[str]] = {}
    try:
        res = subprocess.run(
            ["ip", "-br", "addr"], capture_output=True, text=True, timeout=10
        )
    except (OSError, subprocess.SubprocessError):
        return out
    for line in res.stdout.splitlines():
        fields = line.split()
        if not fields:
            continue
        name = fields[0].split("@")[0]
        v4 = [f.split("/")[0] for f in fields[2:] if re.fullmatch(r"\d+(\.\d+){3}/\d+", f)]
        out[name] = v4
    return out


def _ifaces_from_ipconfig() -> dict[str, list[str]]:
    """Parse `ipconfig` (Windows fallback when psutil is missing)."""
    out: dict[str, list[str]] = {}
    try:
        res = subprocess.run(["ipconfig"], capture_output=True, text=True, timeout=15)
    except (OSError, subprocess.SubprocessError):
        return out
    current = None
    for line in res.stdout.splitlines():
        header = re.match(r"^\S.*?adapter\s+(.+?):\s*$", line)
        if header:
            current = header.group(1).strip()
            out.setdefault(current, [])
            continue
        if current is None:
            continue
        addr = re.search(r"IPv4[^:]*:\s*(\d+(?:\.\d+){3})", line)
        if addr:
            out[current].append(addr.group(1))
    return out


def list_interfaces() -> tuple[dict[str, list[str]], str]:
    """Return {iface_name: [ipv4, ...]} plus the source used, for reporting."""
    via_psutil = _ifaces_from_psutil()
    if via_psutil:
        return via_psutil, "psutil"
    if is_windows():
        return _ifaces_from_ipconfig(), "ipconfig"
    return _ifaces_from_ip_command(), "ip -br addr"


def _in_net(addr: str, net: ipaddress.IPv4Network) -> bool:
    try:
        return ipaddress.ip_address(addr) in net
    except ValueError:
        return False


def is_usable(addr: str) -> bool:
    """Skip loopback and link-local; those can never reach the robot."""
    try:
        ip = ipaddress.ip_address(addr)
    except ValueError:
        return False
    return not (ip.is_loopback or ip.is_link_local or ip.is_unspecified)


def all_local_ipv4(ifaces: dict[str, list[str]]) -> list[str]:
    return [a for addrs in ifaces.values() for a in addrs]


def running_on_g1_dev_pc(ifaces: dict[str, list[str]]) -> bool:
    """True when this process is on the G1 onboard computer (not WSL/laptop)."""
    return DEV_PC_IP in all_local_ipv4(ifaces) and not is_wsl()


def reachability_hosts(ifaces: dict[str, list[str]], robot_ip_arg: str) -> list[tuple[str, str]]:
    """(ip, label) to ping. Avoid treating .164 as remote when we ARE .164."""
    if robot_ip_arg != ROBOT_IP:
        return [(robot_ip_arg, "user --robot-ip")]
    if running_on_g1_dev_pc(ifaces):
        return [(MOTION_BOARD_IP, "motion board (this host is 192.168.123.164)")]
    return [
        (DEV_PC_IP, "G1 development PC / SSH"),
        (MOTION_BOARD_IP, "G1 motion board"),
    ]


def candidate_ifaces(ifaces: dict[str, list[str]]) -> list[str]:
    """Interfaces worth trying, robot-subnet ones first."""
    on_robot_net, others = [], []
    for name, addrs in sorted(ifaces.items()):
        usable = [a for a in addrs if is_usable(a)]
        if not usable:
            continue
        if name.startswith("docker") or name in ("lo", "loopback0"):
            continue
        if any(_in_net(a, ROBOT_NET) for a in usable):
            on_robot_net.append(name)
        else:
            others.append(name)
    return on_robot_net + others


# --------------------------------------------------------------------------
# reachability
# --------------------------------------------------------------------------

def ping(host: str) -> tuple[bool, str]:
    if is_windows():
        cmd = ["ping", "-n", "2", "-w", "1000", host]
    else:
        cmd = ["ping", "-c", "2", "-W", "2", host]
    try:
        res = subprocess.run(cmd, capture_output=True, text=True, timeout=15)
    except FileNotFoundError:
        return False, "ping command not found"
    except subprocess.SubprocessError as exc:
        return False, f"ping failed to run: {exc}"
    tail = [ln.strip() for ln in res.stdout.splitlines() if ln.strip()]
    detail = tail[-1] if tail else "(no output)"
    return res.returncode == 0, detail


def tcp_probe(host: str, port: int = 22, timeout: float = 1.5) -> bool:
    """Extra hint only: the G1 usually answers SSH on 22. Failure proves nothing."""
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def cyclonedds_version() -> str | None:
    try:
        from importlib.metadata import PackageNotFoundError, version
        return version("cyclonedds")
    except PackageNotFoundError:
        return None
    except Exception:  # noqa: BLE001 - metadata problems must not break the report
        return None


# --------------------------------------------------------------------------
# single-interface DDS test (child process)
# --------------------------------------------------------------------------

def try_one_iface(iface: str, timeout: float) -> int:
    sys.path.insert(0, str(SDK_ROOT))
    try:
        from unitree_sdk2py.core.channel import ChannelFactoryInitialize, ChannelSubscriber
        from unitree_sdk2py.idl.unitree_hg.msg.dds_ import LowState_
    except Exception as exc:  # noqa: BLE001 - import errors here are usually env problems
        print(f"    cannot import unitree_sdk2py: {exc}")
        return EXIT_INIT_FAILED

    got = {"msg": False}

    def on_state(_msg):
        got["msg"] = True

    try:
        ChannelFactoryInitialize(0, iface)
        sub = ChannelSubscriber("rt/lowstate", LowState_)
        sub.Init(on_state, 10)
    except Exception as exc:  # noqa: BLE001 - bad iface name raises from CycloneDDS
        print(f"    DDS init failed on {iface}: {exc}")
        return EXIT_INIT_FAILED

    deadline = time.time() + timeout
    while not got["msg"] and time.time() < deadline:
        time.sleep(0.05)
    return EXIT_GOT_LOWSTATE if got["msg"] else EXIT_NO_LOWSTATE


def spawn_iface_test(iface: str, timeout: float) -> tuple[int, str]:
    cmd = [sys.executable, str(Path(__file__).resolve()), "--try-iface", iface,
           "--timeout", str(timeout)]
    try:
        res = subprocess.run(
            cmd, capture_output=True, text=True, timeout=timeout + 20, cwd=str(HERE)
        )
    except subprocess.TimeoutExpired:
        return EXIT_NO_LOWSTATE, "child process hung and was killed"
    output = (res.stdout + res.stderr).strip()
    return res.returncode, output


# --------------------------------------------------------------------------
# report
# --------------------------------------------------------------------------

def main() -> int:
    parser = argparse.ArgumentParser(
        description="Diagnose the DDS link to the G1 (read-only, never publishes)."
    )
    parser.add_argument("--try-iface", metavar="NAME", help=argparse.SUPPRESS)
    parser.add_argument("--timeout", type=float, default=15.0,
                        help="seconds to wait for rt/lowstate per interface")
    parser.add_argument("--robot-ip", default=ROBOT_IP, help=f"override ping target (default {ROBOT_IP})")
    parser.add_argument("--iface", action="append", metavar="NAME",
                        help="only test this interface (repeatable)")
    args = parser.parse_args()

    if args.try_iface:
        code = try_one_iface(args.try_iface, args.timeout)
        sys.stdout.flush()
        # DDS keeps non-daemon threads alive; leave hard so the parent is not blocked.
        os._exit(code)

    host = "Windows" if is_windows() else ("WSL2" if is_wsl() else "Linux")
    print(f"Host: {host}   python: {sys.executable}")
    cdds = cyclonedds_version()
    print(f"cyclonedds: {cdds or 'not installed'}")
    if cdds and not cdds.startswith("0.10"):
        print("  WARNING: unitree_sdk2py pins cyclonedds==0.10.2. Other versions can")
        print("  subscribe without raising and still never deliver data (silent failure).")
    print()

    ifaces, source = list_interfaces()
    print(f"--- interfaces (via {source}) ---")
    if not ifaces:
        print("Could not enumerate interfaces. Install psutil or check that "
              "`ip -br addr` / `ipconfig` works.")
        return 1
    for name, addrs in sorted(ifaces.items()):
        shown = ", ".join(addrs) if addrs else "(no IPv4)"
        tag = ""
        if any(_in_net(a, ROBOT_NET) for a in addrs):
            tag = "   <== robot subnet 192.168.123.0/24"
        elif any(any(_in_net(a, n) for n in WSL_NAT_NETS) for a in addrs):
            tag = "   (WSL NAT-style address)"
        print(f"  {name:24} {shown}{tag}")
    print()

    on_robot_net = [n for n, a in ifaces.items() if any(_in_net(x, ROBOT_NET) for x in a)]
    nat_only = not on_robot_net and any(
        any(_in_net(x, n) for n in WSL_NAT_NETS) for a in ifaces.values() for x in a
    )

    if on_robot_net:
        print(f"OK: {', '.join(on_robot_net)} hold(s) a 192.168.123.x address.")
    else:
        print("WARNING: no interface has an address in 192.168.123.0/24.")
        if nat_only or is_wsl():
            print()
            print("  This looks like WSL2 in default NAT networking mode. WSL's eth0 is a")
            print("  private virtual adapter and cannot see the Windows USB Ethernet adapter")
            print("  that holds 192.168.123.x, so DDS discovery never reaches the G1.")
            print(f"  Fix: see {HERE / 'wslconfig_template.txt'}")
            print("  Copy it to C:\\Users\\vuong\\.wslconfig, then run `wsl --shutdown`")
            print("  from Windows PowerShell/CMD (not inside WSL) and reopen the distro.")
        else:
            print("  Check that the USB-C debug cable is plugged in and the adapter is up.")
    print()

    targets = reachability_hosts(ifaces, args.robot_ip)
    if running_on_g1_dev_pc(ifaces):
        print("This host is 192.168.123.164 (G1 development PC). "
              "Pinging it would only prove loopback.")
        print()
    for ip, label in targets:
        print(f"--- reachability to {ip} ({label}) ---")
        ok, detail = ping(ip)
        print(f"  ping: {'OK' if ok else 'FAILED'}  ({detail})")
        if ip == DEV_PC_IP and not running_on_g1_dev_pc(ifaces):
            if tcp_probe(ip):
                print("  tcp/22: open (SSH answering)")
            else:
                print("  tcp/22: no answer (only a hint; SSH may simply be closed)")
        print()

    candidates = args.iface or candidate_ifaces(ifaces)
    if not candidates:
        print("No candidate interfaces with a usable IPv4 address. Nothing to test.")
        return 1

    print(f"--- DDS test: subscribing rt/lowstate for {args.timeout:g}s per interface ---")
    working: list[str] = []
    for name in candidates:
        print(f"  {name} ...", flush=True)
        code, output = spawn_iface_test(name, args.timeout)
        if output:
            for line in output.splitlines():
                print(f"      {line}")
        if code == EXIT_GOT_LOWSTATE:
            print(f"      lowstate RECEIVED on {name}")
            working.append(name)
        elif code == EXIT_INIT_FAILED:
            print(f"      DDS could not start on {name}")
        else:
            print(f"      no lowstate on {name}")
    print()

    if working:
        best = working[0]
        for name in working:
            if any(_in_net(a, ROBOT_NET) for a in ifaces.get(name, [])):
                best = name
                break
        print("RESULT: DDS link is up.")
        print(f"Use: python3 probe_g1_arm_dof.py {best}")
        return 0

    print("RESULT: no interface received rt/lowstate.")
    print("Checklist:")
    print("  - robot powered on, out of damping/off, development mode engaged")
    print(f"  - on the G1: `ping {MOTION_BOARD_IP}` (motion board), not only {DEV_PC_IP}")
    print("  - prove DDS with the known-good CLI: python3 g1_arm_cli.py eth0 high_wave")
    print("  - nothing else is holding the DDS domain (other SDK scripts, welcome app)")
    if is_wsl():
        print(f"  - WSL2 mirrored networking enabled: see {HERE / 'wslconfig_template.txt'}")
        print("  - USB from WSL usually cannot see rt/lowstate; use g1_arm_bridge_server.py on the G1")
    return 2


if __name__ == "__main__":
    sys.exit(main())
