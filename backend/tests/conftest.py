"""Run the OpenAI integration contract tests independently of local credentials."""
import os

os.environ["TABLOAI_LLM_PROVIDER"] = "openai"
os.environ["TABLOAI_OPENAI_API_KEY"] = ""
os.environ["TABLOAI_GEMINI_API_KEY"] = ""
