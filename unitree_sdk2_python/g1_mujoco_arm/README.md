# Live MuJoCo G1 arms (run on your Linux PC)

MuJoCo stays **on your computer**. USB Ethernet can ping the G1 onboard PC (`192.168.123.164`) but usually **cannot** see `rt/lowstate` (the motion board). Use the on-robot HTTP bridge.

## Recommended: HTTP bridge (WSL + USB)

Keep the robot in **development mode**. Stop welcome-app arm buttons / `arm_mimic_server_right.py` before the live bridge. Legs are never commanded.

**On the G1 first — prove the known-good path** (same `python3` you used when arms moved):

```bash
ping -c 2 192.168.123.161
cd ~/unitree_sdk2_python
python3 g1_arm_cli.py eth0 high_wave
```

If `.161` fails, the motion board is down. If `high_wave` does not move the arms, do not start MuJoCo yet.

Helpers: on the G1, `bash restore_on_g1.sh --wave` (ping + optional wave + bridge). In WSL, after the bridge is ready, `bash run_mujoco_wsl.sh`.

`check_dds_link.py` run **on the G1** pings **`192.168.123.161`**, not `.164` (that is this computer).

**WSL — copy the folder once:**

```bash
scp -r /mnt/c/Users/vuong/PycharmProjects/Mjhome/unitree_sdk2_python/g1_mujoco_arm \
  unitree@192.168.123.164:/home/unitree/unitree_sdk2_python/
```

**SSH on the G1** (leave this running):

```bash
ssh unitree@192.168.123.164
cd /home/unitree/unitree_sdk2_python/g1_mujoco_arm
python3 g1_arm_bridge_server.py eth0 --dof 5
```

If that prints `No rt/lowstate on THIS machine`, you are not on the robot or development mode is off.

**WSL — second terminal, MuJoCo:**

```bash
cd /mnt/c/Users/vuong/PycharmProjects/Mjhome/unitree_sdk2_python/g1_mujoco_arm
python3 live_arm_to_g1.py --bridge http://192.168.123.164:5012 --dof 5
```

Space = arm the real robot (starts disarmed). M = demo wave.

---

Direct DDS from the PC is optional and is what `probe_g1_arm_dof.py eth0` already proved does not work on this USB link.

## 1. USB Ethernet

Plug the Unitree USB-C debug cable into the G1 and the Linux PC.

```bash
ip -br addr
# Look for an interface with 192.168.123.x (often enx... or usb0)
ping -c 2 192.168.123.164
```

That interface name is what you pass to the scripts. **`eth0` is usually the name on the robot, not on the laptop.** Only use `eth0` if `ip -br addr` shows that it is the USB NIC on this PC.

SSH (optional, for other work): `ssh unitree@192.168.123.164`

Not sure which interface, or seeing "No rt/lowstate"? Run `python3 check_dds_link.py` — it lists interfaces, flags the one on 192.168.123.0/24, pings the robot, and tests each interface with a real (read-only) lowstate subscription.

## 2. Running from WSL2 (Windows)

The USB-C debug cable shows up on **Windows** as a USB Ethernet adapter with a 192.168.123.x address. By default WSL2 uses `networkingMode=NAT`, where WSL's `eth0` is a private virtual adapter (172.16–31.x) behind a Hyper-V switch. It cannot see the Windows USB adapter, so DDS discovery packets never reach the G1 and `probe_g1_arm_dof.py` reports "No rt/lowstate" no matter which interface name you pass.

The fix on Windows 11 22H2+ is **mirrored networking**, which lets WSL share the Windows interfaces:

1. Copy [`wslconfig_template.txt`](wslconfig_template.txt) to `C:\Users\<you>\.wslconfig` (merge the keys if the file already exists).
2. From **Windows** PowerShell or CMD — not inside WSL — run `wsl --shutdown`, then reopen the distro. `.wslconfig` is only read when the WSL VM boots.
3. Verify inside WSL:

```bash
ip -br addr          # expect an interface with 192.168.123.x; the name may still be eth0
ping -c 2 192.168.123.164
python3 check_dds_link.py
```

Pass whatever interface `check_dds_link.py` recommends to the other scripts.

If mirrored mode is unavailable or blocked (older Windows build, VPN, or corporate policy), the fallbacks are:

- **Run natively on Windows Python**, but in its own venv: `unitree_sdk2py` pins `cyclonedds==0.10.2` while the existing Windows `.venv` has cyclonedds 11.0.1. Tested here with the robot pingable on 192.168.123.164 from Windows, that combination imports and subscribes **without raising any error** and simply never delivers lowstate — a silent failure, so don't read "no error" as "working".
- **[usbipd-win](https://github.com/dorssel/usbipd-win) passthrough**, attaching the USB Ethernet device directly into WSL so the 192.168.123.x adapter is owned by Linux. Whether a given USB NIC attaches cleanly varies by device and driver.
- **Run a trajectory player on the robot itself over SSH**, with MuJoCo staying on the PC only as an offline authoring tool. This gives up live teleop.

Mirrored mode changes WSL networking globally; if something else breaks, remove `.wslconfig` and `wsl --shutdown` again to return to NAT.

## 3. Python deps on the PC

From `unitree_sdk2_python`:

```bash
pip install mujoco numpy cyclonedds==0.10.2
pip install -e .
```

## 4. Detect 5-DoF vs 7-DoF

```bash
cd unitree_sdk2_python/g1_mujoco_arm
python3 probe_g1_arm_dof.py YOUR_USB_IFACE
```

## 5. Live control

Stand the robot in a clear area. **Do not** run `g1_arm_cli.py`, welcome-app arm buttons, or `arm_mimic_server_right.py` at the same time.

```bash
python3 live_arm_to_g1.py YOUR_USB_IFACE --dof 5
# or --dof 7 if the probe said 7-DoF wrists
```

In the MuJoCo window (must be focused):

| Key | Action |
|-----|--------|
| Space | Arm / disarm sending to the **real** robot (starts **disarmed**) |
| M | Built-in small wave vs free drag in sim |
| Close window | Ramp `arm_sdk` weight to 0 and exit |

Safety: 50 Hz, max ~0.015 rad/tick, 3 s blend-in, auto-release if commands go stale. Legs are never commanded. Gains are soft (`kp=25`, `kd=1`).

## 6. Pose the sim

With demo **off**, use the MuJoCo perturb tool (Ctrl + right-drag on a geom) to move the capsule arms. With demo **on**, a slow wave plays in sim; press Space only when you want the real robot to follow.
