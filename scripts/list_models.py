from google import genai
import os

api_key = os.environ.get("GOOGLE_API_KEY")

if not api_key:
    raise RuntimeError("GOOGLE_API_KEY not set")

client = genai.Client(api_key=api_key)

print("\nAvailable models:\n")

for model in client.models.list():
    print(model.name)