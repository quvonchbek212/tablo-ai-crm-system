"""Responses adapter contract: no network, partial SQL or leaked provider bodies."""
import sys
import os
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.llm_service import OpenAIService, LLMServiceError, LLMUnavailableError, _create_llm_service
from app.config import _first_nonempty


class OpenAIKeyAliasTests(unittest.TestCase):
    def test_selected_openai_provider_is_preserved_without_credentials(self):
        with patch("app.llm_service.settings") as config:
            config.LLM_PROVIDER = "openai"
            config.OPENAI_API_KEY = ""
            config.GEMINI_API_KEY = "unused-test-key"
            self.assertIsInstance(_create_llm_service(), OpenAIService)

    def test_existing_lowercase_key_is_used_when_canonical_field_is_empty(self):
        with patch.dict(os.environ, {"OPENAI_API_KEY": "", "open_ai_api_key": "test-alias-key"}, clear=True):
            self.assertEqual(_first_nonempty("OPENAI_API_KEY", "open_ai_api_key", "OPEN_AI_API_KEY"), "test-alias-key")

    def test_nonempty_canonical_key_takes_precedence(self):
        with patch.dict(os.environ, {"OPENAI_API_KEY": "test-canonical-key", "open_ai_api_key": "test-alias-key"}, clear=True):
            self.assertEqual(_first_nonempty("OPENAI_API_KEY", "open_ai_api_key", "OPEN_AI_API_KEY"), "test-canonical-key")


class GeminiAdapterTests(unittest.IsolatedAsyncioTestCase):
    async def test_temporary_outage_retries_once_and_stays_a_provider_error(self):
        from app.llm_service import GeminiService, LLMUnavailableError
        client = AsyncMock()
        client.__aenter__.return_value = client
        client.post.return_value = MagicMock(status_code=503, headers={"Retry-After": "0"})
        with patch("app.llm_service.httpx.AsyncClient", return_value=client):
            with self.assertRaises(LLMUnavailableError):
                await GeminiService().generate_completion("instructions", "question")
        self.assertEqual(client.post.await_count, 2)

    async def test_quota_error_is_distinct_and_not_retried(self):
        from app.llm_service import GeminiService, LLMRateLimitError
        client = AsyncMock()
        client.__aenter__.return_value = client
        client.post.return_value = MagicMock(status_code=429)
        with patch("app.llm_service.httpx.AsyncClient", return_value=client):
            with self.assertRaises(LLMRateLimitError):
                await GeminiService().generate_completion("instructions", "question")
        client.post.assert_awaited_once()

    async def test_model_list_requires_exact_configured_model(self):
        from app.llm_service import GeminiService
        service = GeminiService()
        service.api_key = "test"
        service.model = "gemini-test"
        client = AsyncMock()
        client.__aenter__.return_value = client
        response = MagicMock(status_code=200)
        client.get.return_value = response
        with patch("app.llm_service.httpx.AsyncClient", return_value=client):
            for model, ready in [("models/gemini-test", True), ("gemini-test-other", False)]:
                response.json.return_value = {"data": [{"id": model}]}
                self.assertEqual((await service.check_health())["model_ready"], ready)


class OpenAIAdapterTests(unittest.IsolatedAsyncioTestCase):
    def service(self):
        service = OpenAIService()
        service.api_key = "test-only-key"
        return service

    def client(self, payload, status=200):
        response = MagicMock(status_code=status)
        response.json.return_value = payload
        response.text = "private provider response"
        client = AsyncMock()
        client.__aenter__.return_value = client
        client.post.return_value = response
        return client

    async def test_responses_payload_and_only_final_text(self):
        client = self.client({"status": "completed", "output": [
            {"type": "reasoning", "summary": [{"text": "never display"}]},
            {"type": "message", "content": [{"type": "output_text", "text": '{"sql_query":"SELECT 1"}'}]},
        ]})
        with patch("app.llm_service.httpx.AsyncClient", return_value=client):
            result = await self.service().generate_completion("instructions", "question")
        self.assertEqual(result, '{"sql_query":"SELECT 1"}')
        args = client.post.call_args
        self.assertTrue(args.args[0].endswith("/responses"))
        self.assertFalse(args.kwargs["json"]["store"])
        self.assertEqual(args.kwargs["json"]["instructions"], "instructions")

    async def test_incomplete_output_rejected(self):
        client = self.client({"status": "incomplete", "output": []})
        with patch("app.llm_service.httpx.AsyncClient", return_value=client):
            with self.assertRaises(LLMServiceError):
                await self.service().generate_completion("", "")

    async def test_missing_key_never_calls_network(self):
        service = self.service()
        service.api_key = ""
        with patch("app.llm_service.httpx.AsyncClient") as mock:
            self.assertFalse((await service.check_health())["model_ready"])
            with self.assertRaises(LLMUnavailableError):
                await service.generate_completion("", "")
        mock.assert_not_called()

    async def test_api_error_body_is_not_exposed(self):
        client = self.client({}, status=401)
        with patch("app.llm_service.httpx.AsyncClient", return_value=client):
            with self.assertRaises(LLMServiceError) as raised:
                await self.service().generate_completion("", "")
        self.assertIn("401", str(raised.exception))
        self.assertNotIn("private provider", str(raised.exception))

    async def test_tool_turn_preserves_calls_reasoning_and_private_replay_contract(self):
        output = [{"type": "reasoning", "encrypted_content": "opaque", "summary": []},
                  {"type": "function_call", "name": "query_data", "call_id": "call_1", "arguments": '{"sql":"SELECT 1"}'}]
        client = self.client({"status": "completed", "output": output})
        input_items = [{"role": "user", "content": "Analyze sales"}]
        tools = [{"type": "function", "name": "query_data", "parameters": {"type": "object"}}]
        with patch("app.llm_service.httpx.AsyncClient", return_value=client):
            result = await self.service().generate_tool_turn("instructions", input_items, tools)
        self.assertEqual(result["output"], output)
        payload = client.post.call_args.kwargs["json"]
        self.assertEqual(payload["input"], input_items)
        self.assertEqual(payload["tools"], tools)
        self.assertFalse(payload["store"])
        self.assertFalse(payload["parallel_tool_calls"])
        self.assertIn("reasoning.encrypted_content", payload["include"])

    async def test_partial_or_failed_tool_calls_never_reach_executor(self):
        for status in ["incomplete", "failed", "in_progress"]:
            with self.subTest(status=status):
                client = self.client({"status": status, "output": [{"type": "function_call", "name": "query_data"}]})
                with patch("app.llm_service.httpx.AsyncClient", return_value=client), self.assertRaises(LLMServiceError):
                    await self.service().generate_tool_turn("", [], [])
