import asyncio
import json
import logging
import re
from typing import Dict, Any, Optional
import httpx
from app.config import settings

logger = logging.getLogger(__name__)


class LLMServiceError(RuntimeError):
    """Base error for a model request that did not produce a usable response."""


class LLMUnavailableError(LLMServiceError):
    """The configured model endpoint is unreachable or timed out."""


class LLMRateLimitError(LLMServiceError):
    """The provider rejected the request with HTTP 429."""

    def __init__(self, provider: str):
        super().__init__(f"{provider} so'rov limiti band (HTTP 429). Limit yangilangach qayta urinib ko'ring.")


class VLLMService:
    provider = "vllm"
    def __init__(self):
        self.base_url = settings.VLLM_BASE_URL.rstrip("/")
        self.api_key = settings.VLLM_API_KEY
        self.model = settings.VLLM_MODEL
        self.timeout = settings.VLLM_TIMEOUT

    @property
    def headers(self) -> Dict[str, str]:
        headers = {
            "Content-Type": "application/json",
            "ngrok-skip-browser-warning": "true"
        }
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        return headers

    async def check_health(self) -> Dict[str, Any]:
        """Check if the configured OpenAI-compatible endpoint and model are ready."""
        health_error: Optional[str] = None
        try:
            timeout_config = httpx.Timeout(connect=10.0, read=10.0, write=10.0, pool=10.0)
            async with httpx.AsyncClient(timeout=timeout_config) as client:
                res = await client.get(f"{self.base_url}/models", headers=self.headers)
                if res.status_code == 200:
                    data = res.json()
                    model_list = [m.get("id") for m in data.get("data", [])]
                    model_found = any(
                        isinstance(model_id, str)
                        and (self.model == model_id or self.model in model_id or model_id in self.model)
                        for model_id in model_list
                    )
                    return {
                        "online": True,
                        "available_models": model_list,
                        "configured_model": self.model,
                        "model_ready": model_found,
                        "provider": "vLLM",
                        "error": None if model_found else "Configured model is not loaded on the endpoint.",
                    }
                else:
                    health_error = f"Model endpoint returned HTTP {res.status_code}."
                    logger.warning(f"vLLM models check returned status: {res.status_code}")
        except Exception as e:
            health_error = f"Model endpoint is unreachable: {type(e).__name__}."
            logger.warning(f"vLLM healthcheck failed: {e}")
        
        return {
            "online": False,
            "available_models": [],
            "configured_model": self.model,
            "model_ready": False,
            "provider": "vLLM",
            "error": health_error or "Model endpoint is unavailable.",
        }

    async def generate_completion(
        self, 
        system_prompt: str, 
        user_prompt: str, 
        temperature: float = 0.0, 
        max_tokens: int = 8192
    ) -> str:
        """Call the selected provider's OpenAI-compatible completion endpoint."""
        url = f"{self.base_url}/chat/completions"
        payload = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt}
            ],
            "temperature": temperature,
            "max_tokens": max_tokens
        }

        try:
            timeout_config = httpx.Timeout(connect=15.0, read=self.timeout, write=15.0, pool=15.0)
            async with httpx.AsyncClient(timeout=timeout_config) as client:
                for transport_attempt in range(2):
                    try:
                        response = await client.post(url, headers=self.headers, json=payload)
                        if response.status_code in (502, 503, 504):
                            retry_after = response.headers.get("Retry-After", "1")
                            if not transport_attempt and isinstance(retry_after, str) and retry_after.isdigit() and int(retry_after) <= 3:
                                await asyncio.sleep(int(retry_after))
                                continue
                            raise LLMUnavailableError(f"{self.provider} vaqtincha javob bera olmadi (HTTP {response.status_code}).")
                        break
                    except httpx.RemoteProtocolError:
                        if transport_attempt:
                            raise
                        logger.warning("Model connection closed before a response; retrying once.")
                if response.status_code == 429:
                    raise LLMRateLimitError(self.provider)
                if response.status_code == 200:
                    data = response.json()
                    choices = data.get("choices", [])
                    if choices:
                        if choices[0].get("finish_reason") == "length":
                            raise LLMServiceError("Model output was truncated at the token limit; incomplete SQL/JSON was rejected.")
                        msg = choices[0].get("message", {})
                        content = msg.get("content", "")
                        # Reasoning is not the final SQL/JSON answer.
                        return content or ""
                    return ""
                else:
                    raise LLMServiceError(f"{self.provider} so'rovi bajarilmadi (HTTP {response.status_code}).")
        except (httpx.ConnectError, httpx.RemoteProtocolError):
            err_msg = f"{self.provider} xizmatiga ulanib bo'lmadi."
            logger.error(err_msg)
            raise LLMUnavailableError(err_msg)
        except httpx.TimeoutException:
            err_msg = f"{self.provider} xizmati {self.timeout} soniya ichida javob bermadi."
            logger.error(err_msg)
            raise LLMUnavailableError(err_msg)
        except LLMServiceError:
            raise
        except Exception as e:
            err_msg = str(e) or type(e).__name__
            logger.error(f"Error calling vLLM: {type(e).__name__} - {err_msg}")
            raise LLMServiceError(f"vLLM chaqiruvida xatolik: {err_msg}")

    def extract_json(self, raw_text: str) -> Dict[str, Any]:
        """
        Robust JSON extractor that parses JSON objects even if surrounded
        by markdown fences, reasoning thoughts, or conversational text.
        """
        text = raw_text.strip()
        
        # Remove reasoning / think blocks if present: <think> ... </think> or unclosed <think> ...
        text = re.sub(r"<think>[\s\S]*?(?:</think>|$)", "", text, flags=re.IGNORECASE).strip()

        # Remove markdown ```json ... ``` or ``` ... ```
        if "```" in text:
            match = re.search(r"```(?:json)?\s*([\s\S]*?)\s*```", text, re.IGNORECASE)
            if match:
                text = match.group(1).strip()

        # Try parsing directly
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            pass

        # Try to find the first { ... } block
        first_brace = text.find("{")
        last_brace = text.rfind("}")
        if first_brace != -1 and last_brace != -1 and last_brace > first_brace:
            sub = text[first_brace:last_brace + 1]
            try:
                return json.loads(sub)
            except json.JSONDecodeError:
                pass

        # Try finding JSON array [ ... ]
        first_bracket = text.find("[")
        last_bracket = text.rfind("]")
        if first_bracket != -1 and last_bracket != -1 and last_bracket > first_bracket:
            sub = text[first_bracket:last_bracket + 1]
            try:
                return json.loads(sub)
            except json.JSONDecodeError:
                pass

        raise ValueError(f"Could not extract valid JSON from LLM response: {raw_text[:300]}...")

class OpenAIService(VLLMService):
    """Server-only Responses API adapter, sharing the validated JSON parser."""

    provider = "openai"

    def __init__(self):
        self.base_url = "https://api.openai.com/v1"
        self.api_key = settings.OPENAI_API_KEY
        self.model = settings.OPENAI_MODEL
        self.timeout = settings.OPENAI_TIMEOUT

    @property
    def headers(self) -> Dict[str, str]:
        return {"Content-Type": "application/json", "Authorization": f"Bearer {self.api_key}"}

    async def check_health(self) -> Dict[str, Any]:
        info = {"provider": self.provider, "configured_model": self.model,
                "online": False, "model_ready": False, "available_models": []}
        if not self.api_key:
            return {**info, "error": "OPENAI_API_KEY sozlanmagan. Namuna tahlil rejimi ishlaydi."}
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                res = await client.get(f"{self.base_url}/models/{self.model}", headers=self.headers)
            if res.status_code == 200:
                return {**info, "online": True, "model_ready": True,
                        "available_models": [self.model], "error": None}
            return {**info, "error": f"OpenAI model tekshiruvi: HTTP {res.status_code}."}
        except httpx.HTTPError:
            return {**info, "error": "OpenAI bilan aloqa o'rnatilmadi."}

    async def generate_completion(self, system_prompt: str, user_prompt: str,
                                  temperature: float = 0.0, max_tokens: int = 8192) -> str:
        if not self.api_key:
            raise LLMUnavailableError("OPENAI_API_KEY sozlanmagan. backend/.env fayliga kalit kiriting.")
        payload = {"model": self.model, "instructions": system_prompt,
                   "input": user_prompt, "max_output_tokens": max_tokens, "store": False}
        try:
            async with httpx.AsyncClient(timeout=httpx.Timeout(self.timeout, connect=15.0)) as client:
                response = await client.post(f"{self.base_url}/responses", headers=self.headers, json=payload)
            if response.status_code != 200:
                # Provider bodies can contain request data; don't forward them to clients/logs.
                raise LLMServiceError(f"OpenAI so'rovi bajarilmadi (HTTP {response.status_code}). Kalit, model va hisob limitini tekshiring.")
            data = response.json()
            if data.get("status") == "incomplete":
                raise LLMServiceError("OpenAI javobi tugallanmagan; qisman SQL bajarilmadi.")
            if data.get("status") not in (None, "completed"):
                raise LLMServiceError("OpenAI so'rovi yakunlanmadi.")
            output = "\n".join(
                part.get("text", "")
                for item in data.get("output", []) if item.get("type") == "message"
                for part in item.get("content", []) if part.get("type") == "output_text"
            ).strip()
            if not output:
                raise LLMServiceError("OpenAI bo'sh javob qaytardi.")
            return output
        except httpx.TimeoutException as exc:
            raise LLMUnavailableError("OpenAI javobini kutish vaqti tugadi.") from exc
        except httpx.HTTPError as exc:
            raise LLMUnavailableError("OpenAI bilan aloqa o'rnatilmadi.") from exc

    async def generate_tool_turn(self, instructions: str, input_items: list, tools: list,
                                 *, tool_choice="auto", max_tokens: int = 6000) -> dict:
        """One Responses turn. Callers replay output items and return tool results.

        No reasoning text is exposed to the UI. Raw output must be preserved in
        the private request history so reasoning models can continue tool calls.
        """
        if not self.api_key:
            raise LLMUnavailableError("OPENAI_API_KEY sozlanmagan.")
        payload = {
            "model": self.model, "instructions": instructions, "input": input_items,
            "tools": tools, "tool_choice": tool_choice, "parallel_tool_calls": False,
            "max_output_tokens": max_tokens, "store": False,
            "include": ["reasoning.encrypted_content"],
        }
        try:
            async with httpx.AsyncClient(timeout=httpx.Timeout(self.timeout, connect=15.0)) as client:
                response = await client.post(f"{self.base_url}/responses", headers=self.headers, json=payload)
            if response.status_code != 200:
                raise LLMServiceError(f"OpenAI agent so'rovi bajarilmadi (HTTP {response.status_code}).")
            data = response.json()
            if not isinstance(data, dict) or data.get("status") != "completed":
                raise LLMServiceError("Agent javobi tugallanmagan; undagi amallar bajarilmadi.")
            if not isinstance(data.get("output"), list) or not data["output"]:
                raise LLMServiceError("OpenAI agenti bo'sh javob qaytardi.")
            return data
        except httpx.TimeoutException as exc:
            raise LLMUnavailableError("OpenAI agent javobini kutish vaqti tugadi.") from exc
        except httpx.HTTPError as exc:
            raise LLMUnavailableError("OpenAI agentiga ulanib bo'lmadi.") from exc
        except (ValueError, TypeError) as exc:
            raise LLMServiceError("OpenAI agent javobini o'qib bo'lmadi.") from exc


class GeminiService(VLLMService):
    """Google Gemini adapter using OpenAI-compatible /chat/completions endpoint."""

    provider = "gemini"

    def __init__(self):
        self.base_url = "https://generativelanguage.googleapis.com/v1beta/openai"
        self.api_key = settings.GEMINI_API_KEY
        self.model = settings.GEMINI_MODEL or "gemini-2.5-flash"
        self.timeout = getattr(settings, "GEMINI_TIMEOUT", 90.0)

    @property
    def headers(self) -> Dict[str, str]:
        return {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self.api_key}",
        }

    async def check_health(self) -> Dict[str, Any]:
        info = {
            "provider": "Gemini",
            "configured_model": self.model,
            "online": False,
            "model_ready": False,
            "available_models": [],
        }
        if not self.api_key:
            return {**info, "error": "GEMINI_API_KEY sozlanmagan."}
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                res = await client.get(f"{self.base_url}/models", headers=self.headers)
            if res.status_code == 200:
                data = res.json()
                models = [m["id"] for m in data.get("data", []) if isinstance(m.get("id"), str)]
                ready = self.model.removeprefix("models/") in {m.removeprefix("models/") for m in models}
                return {
                    **info,
                    "online": True,
                    "model_ready": ready,
                    "available_models": models,
                    "error": None if ready else "Sozlangan Gemini modeli ro'yxatda topilmadi.",
                }
            return {**info, "error": f"Gemini API tekshiruvi: HTTP {res.status_code}"}
        except Exception as e:
            return {**info, "error": f"Gemini bilan aloqa o'rnatilmadi: {e}"}

    async def generate_tool_turn(self, instructions: str, input_items: list, tools: list,
                                 *, tool_choice="auto", max_tokens: int = 6000) -> dict:
        """Call Gemini via OpenAI-compatible tool calling."""
        messages = [{"role": "system", "content": instructions}]
        for item in input_items:
            if isinstance(item, dict):
                messages.append(item)
            elif isinstance(item, str):
                messages.append({"role": "user", "content": item})

        payload = {
            "model": self.model,
            "messages": messages,
            "max_tokens": max_tokens
        }
        if tools:
            payload["tools"] = tools
            payload["tool_choice"] = tool_choice

        try:
            async with httpx.AsyncClient(timeout=httpx.Timeout(self.timeout, connect=15.0)) as client:
                response = await client.post(f"{self.base_url}/chat/completions", headers=self.headers, json=payload)
            if response.status_code != 200:
                raise LLMServiceError(f"Gemini agent so'rovi bajarilmadi: HTTP {response.status_code}")
            data = response.json()
            choice = data["choices"][0]
            msg = choice.get("message", {})
            return {
                "status": "completed",
                "output": [
                    {
                        "type": "message",
                        "content": [{"type": "output_text", "text": msg.get("content", "") or ""}]
                    }
                ]
            }
        except httpx.TimeoutException as exc:
            raise LLMUnavailableError("Gemini agent javobini kutish vaqti tugadi.") from exc
        except httpx.HTTPError as exc:
            raise LLMUnavailableError("Gemini agentiga ulanib bo'lmadi.") from exc


def _create_llm_service():
    provider = settings.LLM_PROVIDER.lower()
    if provider == "gemini":
        return GeminiService()
    elif provider == "vllm":
        return VLLMService()
    elif provider == "openai":
        return OpenAIService()
    return GeminiService() if settings.GEMINI_API_KEY else OpenAIService()


llm_service = _create_llm_service()
