import os
import warnings
from dotenv import load_dotenv

warnings.filterwarnings("ignore")
load_dotenv()


def get_llm(model_name: str, temperature: float):
  """Unified LangChain LLM Factory function.

  Mandatory parameters: model_name and temperature must be provided explicitly.

  Usage:
      llm = get_llm(model_name="gpt-4o-mini", temperature=0)
  """
  provider = os.getenv("LLM_PROVIDER", "openai").strip().lower()

  if provider == "gemini":
    from langchain_google_genai import ChatGoogleGenerativeAI

    gemini_key = os.getenv("GEMINI_API_KEY")
    if not gemini_key:
      raise ValueError("GEMINI_API_KEY is not set in your .env file.")

    return ChatGoogleGenerativeAI(
        model=model_name,
        temperature=temperature,
        google_api_key=gemini_key,
    )

