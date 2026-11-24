"""Small live OpenAI check; never prints credentials or provider response bodies."""
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.llm_service import llm_service, LLMServiceError


async def main():
    print("Provider:", llm_service.provider)
    print("Model:", llm_service.model)
    print("API key loaded:", bool(llm_service.api_key.strip()))
    if llm_service.provider != "openai" or not llm_service.api_key.strip():
        return 1
    try:
        answer = await llm_service.generate_completion(
            "Reply with only the word OK.", "Connection test.", max_tokens=32
        )
        print("Responses API: SUCCESS" if answer.strip() else "Responses API: EMPTY")
        return 0 if answer.strip() else 1
    except LLMServiceError as exc:
        print("Responses API:", str(exc))
        return 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
