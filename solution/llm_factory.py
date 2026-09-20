import os
import warnings
from dotenv import load_dotenv
from openai import OpenAI

'''
How to use:
# 1. Use default provider and default lightweight model
llm = get_llm()

# 2. Or override with a specific model and temperature for complex tasks
vision_llm = get_llm(model_name="gpt-4o", temperature=0.2)

'''
warnings.filterwarnings("ignore")
load_dotenv()

def get_llm():
    provider = os.getenv("LLM_PROVIDER", "openai").lower()

    if provider == "gemini":
        # Returns a native google-genai Client. Callers use
        # client.models.generate_content(model=..., contents=..., config=...).
        from google import genai
        gemini_key = os.getenv("GEMINI_API_KEY")
        if not gemini_key:
            raise ValueError("GEMINI_API_KEY is not set in .env")
        return genai.Client(api_key=gemini_key)

    else:  # Default OpenAI
        openai_key = os.getenv("OPENAI_API_KEY")
        if not openai_key:
            raise ValueError("OPENAI_API_KEY is not set in .env")
        return OpenAI(api_key=openai_key)
