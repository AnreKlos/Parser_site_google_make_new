#!/usr/bin/env python3
"""Проверка доступных моделей Fireworks AI"""
import os
import requests
from dotenv import load_dotenv

load_dotenv()

api_key = os.getenv('FIREWORKS_API_KEY')
print(f"API Key: {api_key[:15]}...{api_key[-5:]}")

# Получаем список моделей
response = requests.get(
    "https://api.fireworks.ai/inference/v1/models",
    headers={"Authorization": f"Bearer {api_key}"}
)

print(f"Status: {response.status_code}")

if response.status_code == 200:
    data = response.json()
    models = data.get('data', [])
    print(f"\nДоступные модели ({len(models)}):")
    for m in models[:20]:  # Показываем первые 20
        print(f"  - {m['id']}")
else:
    print(f"Error: {response.text}")