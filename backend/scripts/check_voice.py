"""Opt-in live audio roundtrip against the running local app (uses OpenAI credits)."""
import argparse
import io
import json
from pathlib import Path
import sys
import wave
import httpx


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default="http://127.0.0.1:8015")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    result = {"passed": False, "scope": "Synthetic audio through OpenAI synthesis and transcription; physical microphone not tested."}
    try:
        with httpx.Client(base_url=args.base_url, timeout=120) as client:
            audio = client.post("/api/platform/voice/speech", json={"text": "Salom. Menga sotuv hisobotini ko'rsat."})
            if audio.status_code != 200:
                raise ValueError(f"Speech HTTP {audio.status_code}")
            with wave.open(io.BytesIO(audio.content), "rb") as wav:
                seconds = wav.getnframes() / wav.getframerate()
                if seconds <= 0:
                    raise ValueError("Empty audio")
            transcript = client.post("/api/platform/voice/transcribe", files={"file": ("check.wav", audio.content, "audio/wav")})
            if transcript.status_code != 200:
                raise ValueError(f"Transcription HTTP {transcript.status_code}")
            text = transcript.json().get("text", "")
            if not text.strip():
                raise ValueError("Empty transcript")
            # This is a transport/content check, not a claim of perfect speech accuracy.
            result.update(passed=True, audio_seconds=round(seconds, 2), transcript=text)
    except Exception as exc:
        result["error"] = str(exc) if isinstance(exc, ValueError) else type(exc).__name__
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print("Voice roundtrip:", "PASS" if result["passed"] else "FAIL")
    if result.get("error"):
        print(result["error"])
    return 0 if result["passed"] else 1


if __name__ == "__main__":
    sys.exit(main())
