import os

import requests


BUSINESSGPT_URL = os.environ.get(
    "BUSINESSGPT_API_URL", "http://businessgpt-api:8000/generate"
)
BUSINESSGPT_API_KEY = os.environ["BUSINESSGPT_API_KEY"]


def ask_businessgpt(messages, system_prompt=None, max_tokens=128, temperature=0.9):
    payload = {
        "messages": messages,
        "max_tokens": max_tokens,
        "temperature": temperature,
        "repetition_penalty": 1.2,
    }
    if system_prompt:
        payload["system_prompt"] = system_prompt

    response = requests.post(
        BUSINESSGPT_URL,
        headers={
            "Authorization": f"Bearer {BUSINESSGPT_API_KEY}",
            "Content-Type": "application/json",
        },
        json=payload,
        timeout=310,
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
