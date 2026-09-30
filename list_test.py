import os
import google.generativeai as genai
from dotenv import load_dotenv

# Load your API key
load_dotenv()
genai.configure(api_key=os.getenv("GEMINI_API_KEY"))

# Fetch and print all available models that support text generation
print("Available Models:")
for m in genai.list_models():
    if 'generateContent' in m.supported_generation_methods:
        print(m.name)