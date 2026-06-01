"""
ROY - GM Capital 실무총괄 사장
텔레그램 고정 명령어 처리 + 원격 스크립트 실행
대화는 폰 Claude 앱 사용 / 개발은 PC VS Code에서
"""
import requests
import json
import os
import sys
import subprocess
import time
from datetime import datetime

sys.stdout.reconfigure(encoding='utf-8')

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
with open(os.path.join(BASE_DIR, 'config.json'), 'r', encoding='utf-8') as f:
    config = json.load(f)

TOKEN   = config['telegram_token']
CHAT_ID = str(config['telegram_chat_id'])
PYTHON  = sys.executable

def send(text):
    requests.post(
        f"https://api.telegram.org/bot{TOKEN}/sendMessage",
        json={'chat_id': CHAT_ID, 'text': text, 'parse_mode': 'Markdown',
              'disable_web_page_preview': True},
        timeout=10
    )

def run_script(name):
    subprocess.Popen([PYTHON, os.path.join(BASE_DIR, name)])

HELP_TEXT = (
    "*ROY 명령어 목록*\n\n"
    "`브리핑` — 뉴스 브리핑 지금 받기\n"
    "`회의` — 팀 회의 보고 지금 받기\n"
    "`리포트` — 저녁 보고 지금 받기\n"
    "`상태` — 시스템 상태 확인\n"
    "`도움말` — 명령어 목록\n\n"
    "_대화·질문은 폰 Claude 앱을 이용해주세요._"
)

def handle(text):
    t = text.strip().lower()

    if t in ('/briefing', '브리핑', 'briefing'):
        send("📡 *INF* 수집 시작 중... 잠시만요, 이사장님.")
        run_script('morning_briefing.py')

    elif t in ('/report', '리포트', '보고', 'report'):
        send("📋 *ROY* 리포트 준비 중...")
        run_script('daily_report.py')

    elif t in ('/meeting', '회의', 'meeting'):
        send("💼 팀 회의 소집 중...")
        run_script('meeting_report.py')

    elif t in ('/status', '상태', 'status'):
        now = datetime.now().strftime('%Y-%m-%d %H:%M')
        send(
            f"*GM Capital 시스템 상태*\n\n"
            f"현재 시각: `{now}`\n"
            f"ROY: 🟢 온라인\n"
            f"INF / DAT / AP / DES: 🟢 대기중\n\n"
            f"📅 06:30 · 09:00 · 15:00 · 18:00 · 21:00 자동 실행"
        )

    elif t in ('/help', '도움말', 'help', '?'):
        send(HELP_TEXT)

    else:
        send(
            f"ROY: *'{text[:30]}'* 명령을 알 수 없습니다.\n"
            f"`도움말` 로 명령어를 확인하거나,\n"
            f"대화·질문은 폰 Claude 앱을 이용해주세요."
        )

def main():
    print(f"[{datetime.now():%Y-%m-%d %H:%M}] ROY 봇 시작")
    send(
        "🟢 *ROY 온라인*\n"
        "이사장님, 명령 대기 중입니다.\n"
        "`도움말` 로 명령어 확인 가능합니다."
    )

    last_update_id = None

    while True:
        try:
            params = {'timeout': 30, 'allowed_updates': ['message']}
            if last_update_id:
                params['offset'] = last_update_id + 1

            resp = requests.get(
                f"https://api.telegram.org/bot{TOKEN}/getUpdates",
                params=params, timeout=35
            ).json()

            for update in resp.get('result', []):
                last_update_id = update['update_id']
                msg  = update.get('message', {})
                chat = msg.get('chat', {})

                if str(chat.get('id')) != CHAT_ID:
                    continue

                text = msg.get('text', '')
                if text:
                    print(f"  [{datetime.now():%H:%M}] {text[:50]}")
                    handle(text)

        except requests.exceptions.Timeout:
            pass
        except Exception as e:
            print(f"  [오류] {e}")
            time.sleep(5)

if __name__ == '__main__':
    main()
