"""
Groq 공통 클라이언트
모든 스크립트가 이 모듈을 통해 Groq API를 호출 — URL/Model 하드코딩 제거
"""
import json, os, requests

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
with open(os.path.join(BASE_DIR, 'config.json'), 'r', encoding='utf-8') as f:
    _cfg = json.load(f)

URL   = 'https://api.groq.com/openai/v1/chat/completions'
MODEL = _cfg.get('groq_model', 'llama-3.3-70b-versatile')
KEY   = _cfg.get('groq_api_key', '')


def call(prompt, max_tokens=600, temperature=0.3):
    """Groq API 단일 호출. 실패 시 Exception 발생."""
    resp = requests.post(
        URL,
        headers={'Authorization': f'Bearer {KEY}', 'Content-Type': 'application/json'},
        json={'model': MODEL,
              'messages': [{'role': 'user', 'content': prompt}],
              'temperature': temperature,
              'max_tokens': max_tokens},
        timeout=30,
    )
    data = resp.json()
    if 'error' in data:
        raise Exception(data['error'].get('message', str(data['error'])))
    return data['choices'][0]['message']['content'].strip()
