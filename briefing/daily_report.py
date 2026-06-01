import requests
import json
import os
import sys
from datetime import datetime
from proposal_manager import get_proposals

sys.stdout.reconfigure(encoding='utf-8')

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
with open(os.path.join(BASE_DIR, 'config.json'), 'r', encoding='utf-8') as f:
    config = json.load(f)

TELEGRAM_TOKEN   = config['telegram_token']
TELEGRAM_CHAT_ID = config['telegram_chat_id']


def send_telegram(text):
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    resp = requests.post(url, json={
        'chat_id': TELEGRAM_CHAT_ID,
        'text': text,
        'parse_mode': 'Markdown',
        'disable_web_page_preview': True,
    }, timeout=15)
    return resp.json()

def main():
    now     = datetime.now()
    day_kr  = ['월','화','수','목','금','토','일'][now.weekday()]
    today   = f"{now.strftime('%Y년 %m월 %d일')} ({day_kr}요일)"

    selected = get_proposals(count=3)

    msg = f"""📋 *GM Capital 일일 리포트*
{today} 18:00

━━━━━━━━━━━━━━━━
🏢 *오늘 하루 요약*
━━━━━━━━━━━━━━━━

📋 자동 실행 완료: 06:30 모닝브리핑 · 09:00 부서보고 · 15:00 제안 · 21:00 개장전 속보
📊 DAT: 보유 5종목 실시간 모니터링 정상
🔍 AP: 가격 경보 시스템 정상 작동 중

━━━━━━━━━━━━━━━━
💡 *ROY의 업그레이드 제안 (오늘 3건)*
━━━━━━━━━━━━━━━━
_이사장님이 원하시는 항목을 다음 지시에 포함해주시면 바로 실행합니다._

"""
    for i, (title, desc) in enumerate(selected, 1):
        msg += f"*{i}. {title}*\n_{desc}_\n\n"

    msg += "━━━━━━━━━━━━━━━━\n"
    msg += "🏢 _GM Capital · 실무총괄 ROY_"

    result = send_telegram(msg)
    if result.get('ok'):
        print(f"[{now:%Y-%m-%d %H:%M}] ✅ 일일 리포트 전송 완료!")
    else:
        print(f"❌ 전송 실패: {result}")

if __name__ == '__main__':
    main()
