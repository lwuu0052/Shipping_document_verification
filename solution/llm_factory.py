import os
import warnings
from dotenv import load_dotenv
from google import genai

warnings.filterwarnings("ignore")
load_dotenv()


def get_llm(model_name: str = "gemini-3.6-flash", temperature: float = 0.0):
  gemini_key = os.getenv("GEMINI_API_KEY")
  if not gemini_key:
    raise ValueError("GEMINI_API_KEY is not set in your .env file.")

  return genai.Client(api_key=gemini_key)
