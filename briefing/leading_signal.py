"""
선행신호 스캐너 — 보유종목 + 관심종목 뉴스 상시 감시
Groq AI: "주가에 아직 미반영된 선행신호인가?" 판단
확신 낮으면 알람 없음. 0건 정상.
"""
import json, os, requests, xml.etree.ElementTree as ET
from datetime import datetime, date

import groq_client

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
LOG_FILE = os.path.join(BASE_DIR, 'signal_log.json')

with open(os.path.join(BASE_DIR, 'config.json'), 'r', encoding='utf-8') as f:
    _cfg = json.load(f)

# 보유종목 + 관심종목 감시 목록
WATCH_LIST = [
    ('TSM',  'TSMC',             'TSMC semiconductor Taiwan foundry CoWoS'),
    ('MU',   'Micron',           'Micron Technology HBM DRAM memory'),
    ('GGLL', 'Alphabet/Google',  'Alphabet Google AI cloud revenue ad'),
    ('IEMG', 'iShares EM ETF',   'emerging market ETF Asia EM'),
    ('SPYM', 'S&P500 ETF',       'S&P 500 market index'),
    ('QLD',  'Nasdaq 2x',        'QQQ Nasdaq technology'),
    # 관심종목
    ('NVDA', 'Nvidia',           'Nvidia GPU AI data center Blackwell'),
    ('AMAT', 'Applied Materials', 'AMAT semiconductor equipment fab'),
    ('ASML', 'ASML',             'ASML EUV lithography chip'),
    ('ARM',  'ARM Holdings',     'ARM semiconductor IP licensing'),
]

_PROMPT = """\
종목: {name}({ticker})
뉴스 헤드라인: {headlines}

위 뉴스 중 "주가에 아직 반영되지 않은 선행신호"가 있는가?

아래 형식으로만 응답. 다른 텍스트 없이:
선행신호: 있음 / 없음
신호내용: (있을 때만 — 30자 이내)
뉴스출처: (있을 때만)
판단: 한 줄

원칙:
- 일반 시황·지수 움직임은 선행신호 아님
- 수주, 파트너십, 규제승인, 설비증설, 공급망 변화, 실적 서프라이즈 등만 해당
- 확신 없으면 없음 (0건 정상 — GM Capital 원칙)"""


def _fetch_news(query, max_items=5):
    url = (f'https://news.google.com/rss/search?q={query.replace(" ", "+")}'
           f'&hl=en-US&gl=US&ceid=US:en')
    try:
        resp  = requests.get(url, timeout=8, headers={'User-Agent': 'Mozilla/5.0'})
        items = ET.fromstring(resp.content).findall('.//item')
        return [it.find('title').text.strip() for it in items[:max_items]
                if it.find('title') is not None]
    except Exception:
        return []


def _parse_response(raw):
    lines = {l.split(':')[0].strip(): ':'.join(l.split(':')[1:]).strip()
             for l in raw.strip().splitlines() if ':' in l}
    has = '있음' in lines.get('선행신호', '없음')
    return has, lines


def _load_today_log():
    today = date.today().isoformat()
    try:
        with open(LOG_FILE, 'r', encoding='utf-8') as f:
            log = json.load(f)
        if log.get('date') == today:
            return log
    except Exception:
        pass
    return {'date': today, 'sent': []}


def _save_log(log):
    with open(LOG_FILE, 'w', encoding='utf-8') as f:
        json.dump(log, f, ensure_ascii=False)


def send_telegram(text):
    token   = _cfg['telegram_token']
    chat_id = _cfg['telegram_chat_id']
    url     = f'https://api.telegram.org/bot{token}/sendMessage'
    requests.post(url, json={'chat_id': chat_id, 'text': text, 'parse_mode': 'HTML'}, timeout=15)


def run_signal_scan():
    print(f'[{datetime.now().strftime("%Y-%m-%d %H:%M")}] 선행신호 스캔 시작...')
    log   = _load_today_log()
    found = []

    for ticker, name, query in WATCH_LIST:
        today_key = f'{ticker}:{date.today().isoformat()}'
        if today_key in log['sent']:
            print(f'  [{ticker}] 오늘 이미 전송 — 건너뜀')
            continue

        headlines = _fetch_news(query)
        if not headlines:
            print(f'  [{ticker}] 뉴스 없음')
            continue

        hl_str = ' / '.join(headlines)
        try:
            raw = groq_client.call(
                _PROMPT.format(ticker=ticker, name=name, headlines=hl_str),
                max_tokens=200, temperature=0.2,
            )
            has, info = _parse_response(raw)
            if has:
                found.append({'ticker': ticker, 'name': name, 'info': info})
                log['sent'].append(today_key)
                print(f'  [{ticker}] ★ 선행신호 감지!')
            else:
                print(f'  [{ticker}] 없음')
        except Exception as e:
            print(f'  [{ticker}] Groq 오류: {e}')

    _save_log(log)

    if not found:
        print('✅ 선행신호 없음 — 전송 안 함 (0건 정상)')
        return

    now_str = datetime.now().strftime('%m/%d %H:%M')
    lines   = [f'<b>🔍 선행신호 포착  {now_str}</b>\n']
    for item in found:
        info = item['info']
        lines.append(f'<b>{item["ticker"]} ({item["name"]})</b>')
        if info.get('신호내용'):
            lines.append(f'신호: {info["신호내용"]}')
        if info.get('뉴스출처'):
            lines.append(f'출처: {info["뉴스출처"]}')
        if info.get('판단'):
            lines.append(f'판단: {info["판단"]}')
        lines.append('')

    lines.append('<i>* Groq AI 선행신호 판단 — AP팀 검수 후 활용 권장</i>')
    lines.append('<i>  확신도 낮으면 알람 없음 (0건 정상)</i>')

    send_telegram('\n'.join(lines))
    print(f'✅ 선행신호 {len(found)}건 전송 완료!')


if __name__ == '__main__':
    run_signal_scan()
