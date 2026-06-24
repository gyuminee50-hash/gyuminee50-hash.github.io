"""
보유종목 모니터링 채널 — 발굴과 분리된 전용 채널
보유 6종목만 감시. 급락 / 중대뉴스 / 매도신호일 때만 알림. 평소 침묵.
06:30 모닝 브리핑과 함께 1회 실행 (발굴 엔진과 별도).
"""
import json, os, requests, xml.etree.ElementTree as ET, yaml
from datetime import datetime, date

import groq_client

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
LOG_FILE = os.path.join(BASE_DIR, 'holdings_log.json')

with open(os.path.join(BASE_DIR, 'config.json'),  'r', encoding='utf-8') as f:
    _api_cfg = json.load(f)
with open(os.path.join(BASE_DIR, 'config.yaml'), 'r', encoding='utf-8') as f:
    _cfg = yaml.safe_load(f)

HOLDINGS = _cfg['universe']['holdings_watch']
JUDGE_MODEL = _cfg['models']['judge']

_HOLDINGS_PROMPT = """\
보유종목: {ticker}
최신 뉴스 헤드라인: {headlines}

아래 질문에 답하라. 형식을 정확히 지켜라:

긴급도: 높음 / 보통 / 낮음
사유: 한 줄 (없으면 "변동 없음")
행동권고: 유지 / 모니터링 / 매도검토 / 불가

기준:
- 높음: 급락 유발 뉴스, 실적 쇼크, 공급망 위기, 규제 제재
- 보통: 중요하지만 즉각 행동 불요
- 낮음: 일반 뉴스, 시황
- 높음이 아니면 알람 불필요 (0건 정상 — GM Capital 원칙)"""


def _fetch_news(ticker, max_items=4):
    url = (f'https://news.google.com/rss/search?q={ticker}+stock'
           f'&hl=en-US&gl=US&ceid=US:en')
    try:
        resp  = requests.get(url, timeout=8, headers={'User-Agent': 'Mozilla/5.0'})
        items = ET.fromstring(resp.content).findall('.//item')
        return ' / '.join(
            it.find('title').text.strip()
            for it in items[:max_items]
            if it.find('title') is not None
        )
    except Exception:
        return ''


def _load_today_log():
    today = date.today().isoformat()
    try:
        with open(LOG_FILE, 'r', encoding='utf-8') as f:
            log = json.load(f)
        if log.get('date') == today:
            return log
    except Exception:
        pass
    return {'date': today, 'alerted': []}


def _save_log(log):
    with open(LOG_FILE, 'w', encoding='utf-8') as f:
        json.dump(log, f, ensure_ascii=False)


def _send_telegram(text):
    token   = _api_cfg['telegram_token']
    chat_id = _api_cfg['telegram_chat_id']
    requests.post(
        f'https://api.telegram.org/bot{token}/sendMessage',
        json={'chat_id': chat_id, 'text': text, 'parse_mode': 'HTML'},
        timeout=15,
    )


def run_holdings_monitor():
    """보유종목 중대뉴스 감시. 긴급도 높음만 알림."""
    print(f'[{datetime.now().strftime("%Y-%m-%d %H:%M")}] 보유종목 모니터링...')
    log   = _load_today_log()
    alerts = []

    for ticker in HOLDINGS:
        if ticker in log['alerted']:
            print(f'  [{ticker}] 오늘 이미 알림 — 건너뜀')
            continue

        headlines = _fetch_news(ticker)
        if not headlines:
            print(f'  [{ticker}] 뉴스 없음')
            continue

        try:
            raw = groq_client.call(
                _HOLDINGS_PROMPT.format(ticker=ticker, headlines=headlines),
                max_tokens=150, temperature=0.2,
                model=JUDGE_MODEL,
            )
            lines = {l.split(':')[0].strip(): ':'.join(l.split(':')[1:]).strip()
                     for l in raw.strip().splitlines() if ':' in l}
            urgency = lines.get('긴급도', '낮음')

            if urgency == '높음':
                alerts.append({'ticker': ticker, 'info': lines})
                log['alerted'].append(ticker)
                print(f'  [{ticker}] ⚠ 긴급 감지!')
            else:
                print(f'  [{ticker}] 긴급도 {urgency} — 침묵')
        except Exception as e:
            print(f'  [{ticker}] Groq 오류: {e}')

    _save_log(log)

    if not alerts:
        print('✅ 보유종목 중대뉴스 없음 (0건 정상)')
        return

    now_str = datetime.now().strftime('%m/%d %H:%M')
    lines   = [f'<b>⚠️ 보유종목 긴급 알림  {now_str}</b>\n']
    for a in alerts:
        info = a['info']
        lines.append(f'<b>{a["ticker"]}</b>')
        lines.append(f'사유: {info.get("사유", "-")}')
        lines.append(f'행동권고: {info.get("행동권고", "-")}')
        lines.append('')
    lines.append('<i>* 보유종목 전용 채널 — 긴급도 높음만 발송</i>')

    _send_telegram('\n'.join(lines))
    print(f'✅ 보유종목 알림 {len(alerts)}건 발송!')


if __name__ == '__main__':
    run_holdings_monitor()
