from openai import OpenAI

api_url = 'http://localhost:11434/v1'
testing_model = 'hermes3:8b-llama3.1-q4_K_M'
prompt_message = "Hi, i m checking that you are working, tell me something funny and short"

client = OpenAI(base_url=api_url, api_key='ollama')

print("\nSending test request to the local model...")
print("-" * 40)
print(prompt_message)
print("-" * 40)
try:
    response = client.chat.completions.create(
        model=testing_model,
        messages=[{"role": "user", "content": prompt_message}]
    )

    print("\nSuccess")
    print("-" * 40)
    print(response.choices[0].message.content)
    print("-" * 40)
except Exception as e:
    print(f"\nException while connection: {e}")
    print("Check, that Ollama run on your mac")
