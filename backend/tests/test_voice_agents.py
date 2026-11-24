import base64
import io
import json
import sys
import tempfile
import unittest
import wave
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from fastapi import FastAPI
from fastapi.testclient import TestClient
from app import voice, platform_api, platform_service


class VoiceRoutesTests(unittest.TestCase):
    def setUp(self):
        self.key_patch = patch.object(voice.llm_service, "api_key", "test-only-key")
        self.key_patch.start()
        app = FastAPI()
        app.include_router(voice.router)
        self.client = TestClient(app)

    def tearDown(self):
        self.key_patch.stop()

    def test_transcribe_upload_and_filename_normalization(self):
        transcribe = AsyncMock(return_value="Sotuv qancha?")
        with patch.object(voice, "transcribe_audio", new=transcribe):
            response = self.client.post("/api/platform/voice/transcribe", files={"file": ("private-name.webm", b"audio", "audio/webm")})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["text"], "Sotuv qancha?")
        self.assertEqual(transcribe.call_args.args[1], "recording.webm")

    def test_invalid_empty_and_oversized_uploads_do_not_call_provider(self):
        with patch.object(voice, "transcribe_audio", new=AsyncMock()) as call:
            for filename, content, status in [("x.exe", b"x", 415), ("x.wav", b"", 400), ("x.wav", b"x" * (voice.MAX_UPLOAD + 1), 413)]:
                response = self.client.post("/api/platform/voice/transcribe", files={"file": (filename, content)})
                self.assertEqual(response.status_code, status)
        call.assert_not_called()

    def test_missing_key_is_explicit(self):
        with patch.object(voice.llm_service, "api_key", ""):
            self.assertEqual(self.client.post("/api/platform/voice/speech", json={"text": "Salom"}).status_code, 503)

    def test_speech_bounds_and_safe_errors(self):
        self.assertEqual(self.client.post("/api/platform/voice/speech", json={"text": "x" * 2001}).status_code, 422)
        self.assertEqual(self.client.post("/api/platform/voice/speech", json={"text": "   "}).status_code, 400)
        with patch.object(voice, "synthesize_speech", new=AsyncMock(side_effect=RuntimeError("private-secret"))):
            response = self.client.post("/api/platform/voice/speech", json={"text": "Salom"})
        self.assertEqual(response.status_code, 502)
        self.assertNotIn("private-secret", response.text)

    def test_speech_response_is_audio_not_json(self):
        with patch.object(voice, "synthesize_speech", new=AsyncMock(return_value=b"RIFFaudio")):
            response = self.client.post("/api/platform/voice/speech", json={"text": "Salom"})
        self.assertEqual(response.headers["content-type"], "audio/wav")
        self.assertEqual(response.headers["cache-control"], "no-store")


class RealtimeSpeechTests(unittest.IsolatedAsyncioTestCase):
    async def test_audio_deltas_become_valid_wav(self):
        pcm = b"\x00\x01" * 2400
        ws = AsyncMock()
        ws.recv.side_effect = [json.dumps({"type": "session.updated"}),
            json.dumps({"type": "response.output_audio.delta", "delta": base64.b64encode(pcm).decode()}),
            json.dumps({"type": "response.done", "response": {"status": "completed"}})]
        connection = MagicMock()
        connection.__aenter__ = AsyncMock(return_value=ws)
        connection.__aexit__ = AsyncMock(return_value=False)
        with patch.object(voice.llm_service, "api_key", "test-only-key"), patch.object(voice, "connect", return_value=connection):
            result = await voice.synthesize_speech("Salom")
        with wave.open(io.BytesIO(result), "rb") as wav:
            self.assertEqual(wav.getframerate(), 24000)
            self.assertEqual(wav.readframes(2400), pcm)
        self.assertEqual(ws.send.await_count, 2)


class AgentInterpretationTests(unittest.TestCase):
    def test_ai_analysis_is_saved_and_provider_failure_keeps_findings(self):
        with tempfile.TemporaryDirectory() as folder, patch.object(platform_service, "STATE_PATH", Path(folder) / "state.db"), patch.object(platform_api.llm_service, "api_key", "test-only-key"):
            app = FastAPI()
            app.include_router(platform_api.router)
            client = TestClient(app)
            with patch.object(platform_api, "analyze_agent_run", new=AsyncMock(return_value={"summary": "Tekshiruv xulosasi", "recommendations": ["Zaxirani tekshiring"], "mode": "openai"})):
                good = client.post("/api/platform/agents/inventory/run", json={}).json()
            self.assertEqual(good["mode"], "openai")
            self.assertEqual(good["analysis"], "Tekshiruv xulosasi")
            stored = client.get("/api/platform/agents").json()["runs"][0]
            self.assertEqual(stored["analysis"], good["analysis"])
            with patch.object(platform_api, "analyze_agent_run", new=AsyncMock(side_effect=RuntimeError("private-provider-data"))):
                failure = client.post("/api/platform/agents/sales/run", json={}).json()
            self.assertEqual(failure["mode"], "rule_based")
            self.assertTrue(failure["analysis_error"])
            self.assertTrue(failure["findings"])
            self.assertNotIn("private-provider-data", json.dumps(failure))
