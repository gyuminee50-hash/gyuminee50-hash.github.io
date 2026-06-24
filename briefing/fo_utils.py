"""
패밀리오피스 공통 유틸 — status JSON 저장 + 텔레그램 모바일 포맷
"""
import json, os, requests
from datetime import datetime

BASE_DIR    = os.path.dirname(os.path.abspath(__file__))
STATUS_FILE = os.path.join(BASE_DIR, 'family_office_status.json')

with open(os.path.join(BASE_DIR, 'config.json'), 'r', encoding='utf-8') as f:
    _cfg = json.load(f)


def save_status(team: str, data: dict):
    """팀 실행 결과를 family_office_status.json에 저장."""
    try:
        try:
            with open(STATUS_FILE, 'r', encoding='utf-8') as f:
                status = json.load(f)
        except Exception:
            status = {}
        data['updated_at'] = datetime.now().isoformat()
        status[team] = data
        status['last_updated'] = datetime.now().isoformat()
        with open(STATUS_FILE, 'w', encoding='utf-8') as f:
            json.dump(status, f, ensure_ascii=False, indent=2)
    except Exception as e:
        print(f'  [status 저장 오류] {e}')


def send(text: str):
    """텔레그램 발송 (4096자 분할)."""
    token   = _cfg['telegram_token']
    chat_id = _cfg['telegram_chat_id']
    for i in range(0, len(text), 4000):
        requests.post(
            f'https://api.telegram.org/bot{token}/sendMessage',
            json={'chat_id': chat_id, 'text': text[i:i+4000], 'parse_mode': 'HTML'},
            timeout=15,
        )
