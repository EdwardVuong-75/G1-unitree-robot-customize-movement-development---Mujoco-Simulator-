import asyncio
import subprocess
from pathlib import Path

import edge_tts

BASE_DIR = Path(__file__).resolve().parent
AUDIO_DIR = BASE_DIR / "audio"

TEXTS = {
    "phrase1": "Welcome to MJ Home, your cozy and future-ready space.",
    "phrase2": "We are MJ Home, building a sustainable, long-term future for everyone.",
    "phrase3": "MJ Home is proud to serve our community and help transform New Zealand.",
}

VOICE = "en-US-JennyNeural"

async def main():
    AUDIO_DIR.mkdir(exist_ok=True)

    for name, text in TEXTS.items():
        mp3_path = AUDIO_DIR / f"{name}.mp3"
        wav_path = AUDIO_DIR / f"{name}.wav"

        print(f"Generating {wav_path}: {text}")
        communicate = edge_tts.Communicate(text, VOICE, rate="-8%", volume="+0%")
        await communicate.save(str(mp3_path))

        subprocess.run([
            "ffmpeg", "-y",
            "-i", str(mp3_path),
            "-ar", "16000",
            "-ac", "1",
            str(wav_path),
        ], check=True)

    print("Done. Generated:")
    for wav in sorted(AUDIO_DIR.glob("phrase*.wav")):
        print(" -", wav)

if __name__ == "__main__":
    asyncio.run(main())
