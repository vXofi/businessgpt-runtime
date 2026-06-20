import requests


BUSINESSGPT_URL = "https://businessgpt-api.example.com/generate"
BUSINESSGPT_API_KEY = "replace-with-token"


def ask_businessgpt(messages, max_tokens=128, temperature=0.9):
    response = requests.post(
        BUSINESSGPT_URL,
        headers={
            "Authorization": f"Bearer {BUSINESSGPT_API_KEY}",
            "Content-Type": "application/json",
        },
        json={
            "messages": messages,
            "max_tokens": max_tokens,
            "temperature": temperature,
            "repetition_penalty": 1.2,
        },
        timeout=180,
    )
    response.raise_for_status()
    return response.json()["response"]


if __name__ == "__main__":
    print(
        ask_businessgpt(
            [
                {"role": "user", "name": "example-user", "content": "сосал?"},
            ],
            max_tokens=64,
        )
    )

