"""
voice_control.py

Offline speech-to-text voice control for the MJHome G1 Welcome Project.

Uses Vosk (fully offline, no internet needed) to transcribe microphone audio,
then matches command phrases against your existing robot control functions
in app.py: move_robot_command(cmd), arm_command(action_name), say_phrase(choice).

--- Setup (G1 has no internet — do the pip/model download on another machine
    and transfer over LAN, per the offline install steps already worked out) ---

1. On the G1, after transferring wheels + model:
       pip3 install --user --no-index --find-links=. vosk sounddevice

2. Set the model path:
       export VOSK_MODEL_PATH=/home/unitree/vosk-model-small-en-us-0.15

3. Find the correct microphone device index (NOT the camera index —
   DEFAULT_CAMERA_ID in app.py is unrelated to audio):
       python3 -c "import sounddevice as sd; print(sd.query_devices())"
   Set MIC_DEVICE_INDEX if the default input device isn't the mic you want.

--- Integration into app.py ---
See the bottom of this file for the exact snippet to paste in.
"""

import os
import json
import queue
import threading
import time

import sounddevice as sd
from vosk import Model, KaldiRecognizer

VOSK_MODEL_PATH = os.environ.get("VOSK_MODEL_PATH", "")
MIC_DEVICE_INDEX = os.environ.get("MIC_DEVICE_INDEX", "")
SAMPLE_RATE = 16000

# --- Command grammar --------------------------------------------------------
# Keyword-based matching against phrases in your actual app.py vocabulary:
#   - "move"   -> move_robot_command(value)   values: stop/forward/back/left/right/turnleft/turnright
#   - "arm"    -> arm_command(value)          values: any name in ARM_ACTION_NAMES
#   - "say"    -> say_phrase(value)           values: "1"/"2"/"3"/"4"/"random"
#   - "motion" -> motion toggle                value: "on" / "off"
#
# Order matters: more specific phrases are checked first.
COMMAND_TABLE = [
    ("move", "stop", ["stop", "halt", "freeze"]),
    ("move", "forward", ["move forward", "go forward", "walk forward", "forward"]),
    ("move", "back", ["move back", "go back", "walk back", "backward", "back up"]),
    ("move", "turnleft", ["turn left"]),
    ("move", "turnright", ["turn right"]),
    ("move", "left", ["move left", "go left", "left"]),
    ("move", "right", ["move right", "go right", "right"]),

    ("arm", "hands up", ["hands up", "raise your hands", "arms up"]),
    ("arm", "high wave", ["wave", "wave hello", "say hi"]),
    ("arm", "shake hand", ["shake hands", "shake my hand", "handshake"]),
    ("arm", "hug", ["give me a hug", "hug me", "hug"]),
    ("arm", "high five", ["high five"]),
    ("arm", "clap", ["clap"]),
    ("arm", "right hand up", ["right hand up"]),
    ("arm", "release arm", ["release arm", "lower your arm", "put your arm down"]),

    ("motion", "on", ["enable motion", "motion on", "start motion"]),
    ("motion", "off", ["disable motion", "motion off", "stop motion"]),

    ("say", "1", ["say welcome", "say hello", "introduce yourself", "greet"]),
]


def match_command(transcript: str):
    """Return (kind, value) for the first matching command, or None."""
    text = transcript.lower().strip()
    if not text:
        return None
    for kind, value, phrases in COMMAND_TABLE:
        for phrase in phrases:
            if phrase in text:
                return kind, value
    return None


class VoiceController:
    """
    Background listener that turns speech into robot commands.

    Wire it up with your existing functions from app.py:

        vc = VoiceController(
            move_fn=move_robot_command,       # move_fn("forward") -> (ok, msg)
            arm_fn=arm_command,                # arm_fn("shake hand") -> (ok, msg)
            say_fn=say_phrase,                 # say_fn("1") -> (ok, msg)
            motion_toggle_fn=set_motion_enabled,   # motion_toggle_fn(True/False)
            log_fn=add_log,
        )
        vc.start()
        ...
        vc.stop()
    """

    def __init__(self, move_fn, arm_fn, say_fn, motion_toggle_fn, log_fn=print):
        self.move_fn = move_fn
        self.arm_fn = arm_fn
        self.say_fn = say_fn
        self.motion_toggle_fn = motion_toggle_fn
        self.log_fn = log_fn

        self._audio_q = queue.Queue()
        self._thread = None
        self._running = False
        self._model = None
        self._last_transcript = ""
        self._last_command_time = 0.0
        self._command_cooldown = 1.0  # seconds, avoid double-firing on one utterance

    # -- lifecycle ------------------------------------------------------

    def start(self):
        if self._running:
            return True, "Voice control already running."

        if not VOSK_MODEL_PATH or not os.path.isdir(VOSK_MODEL_PATH):
            return False, (
                "VOSK_MODEL_PATH is not set or invalid. Point it at the "
                "unzipped Vosk model folder (see top of voice_control.py)."
            )

        try:
            if self._model is None:
                self.log_fn("Loading Vosk model...")
                self._model = Model(VOSK_MODEL_PATH)
        except Exception as e:
            return False, f"Failed to load Vosk model: {e}"

        self._running = True
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()
        self.log_fn("Voice control started.")
        return True, "Voice control started."

    def stop(self):
        self._running = False
        self.log_fn("Voice control stopping...")
        return True, "Voice control stopped."

    @property
    def is_running(self):
        return self._running

    @property
    def last_transcript(self):
        return self._last_transcript

    # -- internals --------------------------------------------------------

    def _audio_callback(self, indata, frames, time_info, status):
        if status:
            self.log_fn(f"Voice audio status: {status}")
        self._audio_q.put(bytes(indata))

    def _run(self):
        recognizer = KaldiRecognizer(self._model, SAMPLE_RATE)

        device = None
        if MIC_DEVICE_INDEX != "":
            try:
                device = int(MIC_DEVICE_INDEX)
            except ValueError:
                self.log_fn(f"Invalid MIC_DEVICE_INDEX: {MIC_DEVICE_INDEX}")

        try:
            with sd.RawInputStream(
                samplerate=SAMPLE_RATE,
                blocksize=8000,
                dtype="int16",
                channels=1,
                device=device,
                callback=self._audio_callback,
            ):
                self.log_fn("Voice control listening...")
                while self._running:
                    try:
                        data = self._audio_q.get(timeout=0.5)
                    except queue.Empty:
                        continue

                    if recognizer.AcceptWaveform(data):
                        result = json.loads(recognizer.Result())
                        transcript = result.get("text", "")
                        if transcript:
                            self._last_transcript = transcript
                            self.log_fn(f"Voice heard: \"{transcript}\"")
                            self._dispatch(transcript)
        except Exception as e:
            self.log_fn(f"Voice control error: {e}")
        finally:
            self._running = False
            self.log_fn("Voice control stopped.")

    def _dispatch(self, transcript: str):
        now = time.time()
        if now - self._last_command_time < self._command_cooldown:
            return

        matched = match_command(transcript)
        if matched is None:
            return
        kind, value = matched

        ok, msg = self._execute(kind, value)
        self.log_fn(f"Voice command ({kind}: {value}) -> {msg}")
        if ok:
            self._last_command_time = now

    def _execute(self, kind: str, value: str):
        if kind == "move":
            return self.move_fn(value)
        if kind == "arm":
            return self.arm_fn(value)
        if kind == "say":
            return self.say_fn(value)
        if kind == "motion":
            enabled = value == "on"
            self.motion_toggle_fn(enabled)
            return True, f"Motion {'enabled' if enabled else 'disabled'} by voice."
        return False, f"Unhandled command kind: {kind}"


# ---------------------------------------------------------------------------
# Integration snippet for app.py — see chat reply for the exact diff to make.
# ---------------------------------------------------------------------------
