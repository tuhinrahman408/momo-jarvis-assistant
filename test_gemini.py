import os
import time

from dotenv import load_dotenv

load_dotenv()
from google import genai
from google.genai import types

client = genai.Client(
    api_key=os.getenv("GEMINI_API_KEY"),
    http_options=types.HttpOptions(timeout=15000),
)

candidates = [
    "gemini-flash-latest",
    "gemini-3.1-flash-lite",
    "gemini-flash-lite-latest",
    "gemini-2.5-flash-lite",
    "gemini-3.5-flash-lite",
    "gemini-3.5-flash",
    "gemini-2.5-flash",
]

for model in candidates:
    t = time.time()
    try:
        r = client.models.generate_content(model=model, contents="Say hi in one word")
        print("OK  ", model, "|", round(time.time() - t, 1), "seconds |", (r.text or "").strip())
    except Exception as e:
        print("FAIL", model, "|", type(e).__name__, str(e)[:90])
