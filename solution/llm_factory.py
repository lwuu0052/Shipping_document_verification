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
        return "undefined"
    
    else:  # Default OpenAI
        openai_key = os.getenv("OPENAI_API_KEY")
        if not openai_key:
            raise ValueError("OPENAI_API_KEY is not set in .env")
        return OpenAI(api_key=openai_key)