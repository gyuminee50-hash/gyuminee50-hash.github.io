"""
주간 포트폴리오 트래킹 — 매주 월요일 발송
6종목 주간 등락률 + 원인 분석 + 인과율(AI 추정)
"""
import json, os, re, requests, xml.etree.ElementTree as ET
from datetime import datetime, timedelta

import yfinance as yf
import groq_client

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
with open(os.path.join(BASE_DIR, 'config.json'), 'r', encoding='utf-8') as f:
    _cfg = json.load(f)

# 종목 정의: symbol = 실제 티커, news_query = 뉴스 검색어
PORTFOLIO = [
    {'name': 'TSMC',    'symbol': 'TSM',   'news_query': 'TSMC semiconductor'},
    {'name': 'Micron',  'symbol': 'MU',    'news_query': 'Micron Technology MU stock'},
    {'name': 'GGLL',    'symbol': 'GGLL',  'news_query': 'Alphabet Google GOOGL stock'},
    {'name': 'IEMG',    'symbol': 'IEMG',  'news_query': 'iShares emerging markets IEMG'},
    {'name': 'S&P500',  'symbol': 'SPY',   'news_query': 'S&P 500 stock market'},
    {'name': 'QQQ',     'symbol': 'QQQ',   'news_query': 'Nasdaq QQQ tech ETF'},
]

# ── 주간 등락률 ───────────────────────────────────────────────────────
def get_weekly_change(symbol, last_monday, last_friday):
    """지난주 월요일 시가 → 금요일 종가 기준 등락률"""
    try:
        tk = yf.Ticker(symbol)
        hist = tk.history(
            start=last_monday.strftime('%Y-%m-%d'),
            end=(last_friday + timedelta(days=1)).strftime('%Y-%m-%d')
        )
        if hist.empty or len(hist) < 1:
            return None, None, None
        open_price  = hist['Open'].iloc[0]
        close_price = hist['Close'].iloc[-1]
        change_pct  = (close_price - open_price) / open_price * 100
        return round(change_pct, 2), round(open_price, 2), round(close_price, 2)
    except Exception as e:
        print(f'  [주가 오류 {symbol}] {e}')
        return None, None, None


# ── 뉴스 수집 ─────────────────────────────────────────────────────────
def get_stock_news(news_query, days=7):
    """Google News RSS로 지난 7일 헤드라인 수집"""
    query = news_query.replace(' ', '+')
    url = (f'https://news.google.com/rss/search?q={query}'
           f'&hl=en-US&gl=US&ceid=US:en')
    try:
        resp = requests.get(url, timeout=10,
                            headers={'User-Agent': 'Mozilla/5.0'})
        root = ET.fromstring(resp.content)
        headlines = []
        for item in root.findall('.//item')[:20]:
            t = item.find('title')
            if t is not None and t.text:
                headlines.append(t.text.strip())
        return headlines[:8]
    except Exception as e:
        print(f'  [뉴스 오류] {e}')
        return []


# ── 원인 분석 + 인과율 ────────────────────────────────────────────────
_ANALYSIS_PROMPT = """\
이번 주 {name}({symbol})는 {change:+.1f}% {direction}했다.
아래는 수집된 관련 뉴스 헤드라인 {count}건이다.

{headlines}

아래 JSON 형식으로만 응답해. 다른 텍스트 없이:
{{
  "causes": ["원인1 (35자 이내)", "원인2 (35자 이내)"],
  "confidence": 75,
  "confidence_reason": "인과율 근거 (28자 이내)"
}}

인과율(confidence) 산정 기준:
- 85% 이상 : 명확한 직접 원인 (실적 발표, 애널리스트 등급 변경, 기업 공시)
- 65~84%  : 연관 뉴스 다수, 상관성 높음
- 45~64%  : 섹터·매크로 영향 추정, 간접 연관
- 45% 미만 : 뉴스-주가 연관성 불분명 (기술적 흐름 또는 데이터 부족)

주의:
- 한자(漢字) 금지, 순한글로
- 원인은 투자자가 바로 이해할 수 있는 완전한 문장으로
- 단정하지 말고 "~한 영향" "~우려 반영" 등 분석 어조 유지"""


def analyze_cause(name, symbol, change_pct, headlines):
    if not headlines:
        direction = '상승' if change_pct >= 0 else '하락'
        return [f'관련 뉴스 없음 — {direction} 원인 분석 불가'], 0, '뉴스 데이터 없음'

    direction = '상승' if change_pct >= 0 else '하락'
    hl_text   = '\n'.join(f'- {h}' for h in headlines)
    prompt    = _ANALYSIS_PROMPT.format(
        name=name, symbol=symbol,
        change=change_pct, direction=direction,
        count=len(headlines), headlines=hl_text
    )
    try:
        raw = groq_client.call(prompt, max_tokens=500)
        m   = re.search(r'\{.*\}', raw, re.DOTALL)
        if not m:
            raise ValueError('JSON 없음')
        data       = json.loads(m.group())
        causes     = [c for c in data.get('causes', []) if c][:2]
        confidence = max(0, min(100, int(data.get('confidence', 50))))
        reason     = data.get('confidence_reason', '')
        return causes, confidence, reason
    except Exception as e:
        print(f'  [분석 오류 {symbol}] {e}')
        return [f'{direction} 원인 분석 실패'], 50, '파싱 오류'


# ── 인과율 바 시각화 ──────────────────────────────────────────────────
def _confidence_bar(pct):
    """████░░░░ 형식 10칸 바"""
    filled = round(pct / 10)
    return '█' * filled + '░' * (10 - filled)


def _confidence_label(pct):
    if pct >= 85:
        return '직접 원인 확인'
    elif pct >= 65:
        return '연관성 높음'
    elif pct >= 45:
        return '간접 영향 추정'
    else:
        return '연관성 불분명'


# ── 메시지 포맷 ───────────────────────────────────────────────────────
def format_weekly_report(results, week_str):
    lines = [f'<b>📊 주간 포트폴리오 리포트</b>  <i>{week_str}</i>\n']

    for item in results:
        name       = item['name']
        symbol     = item['symbol']
        change_pct = item['change_pct']
        causes     = item['causes']
        confidence = item['confidence']
        conf_reason = item['conf_reason']
        open_p     = item['open']
        close_p    = item['close']

        if change_pct is None:
            lines.append(f'<b>— {name} ({symbol})</b>  데이터 없음\n')
            continue

        arrow = '▲' if change_pct >= 0 else '▼'
        sign  = '+' if change_pct >= 0 else ''
        price_info = f'  <i>{open_p} → {close_p}</i>' if open_p else ''

        lines.append(f'<b>{arrow} {name}  {sign}{change_pct:.1f}%</b>{price_info}')

        for c in causes:
            lines.append(f'  • {c}')

        bar   = _confidence_bar(confidence)
        label = _confidence_label(confidence)
        lines.append(f'  <i>인과율 {confidence}%  {bar}  {label}</i>')
        if conf_reason:
            lines.append(f'  <i>└ {conf_reason}</i>')
        lines.append('')

    lines.append('<i>※ 인과율은 뉴스-주가 연관성 AI 추정치입니다.</i>')
    lines.append('<i>   최종 판단은 AP팀 검수 후 활용 권장.</i>')
    return '\n'.join(lines)


# ── 텔레그램 전송 ─────────────────────────────────────────────────────
def send_telegram(text):
    token   = _cfg['telegram_token']
    chat_id = _cfg['telegram_chat_id']
    url     = f'https://api.telegram.org/bot{token}/sendMessage'
    resp    = requests.post(url,
        json={'chat_id': chat_id, 'text': text, 'parse_mode': 'HTML'},
        timeout=15)
    if not resp.ok:
        print(f'  [텔레그램 오류] {resp.text}')


# ── 메인 ─────────────────────────────────────────────────────────────
def run_weekly_report():
    today       = datetime.today()
    last_monday = today - timedelta(days=today.weekday() + 7)
    last_friday = last_monday + timedelta(days=4)
    week_str    = (f'{last_monday.month}/{last_monday.day}'
                   f'~{last_friday.month}/{last_friday.day}')

    print(f'[{datetime.now().strftime("%Y-%m-%d %H:%M")}] 주간 리포트 수집 시작... ({week_str})')

    results = []
    for stock in PORTFOLIO:
        name   = stock['name']
        symbol = stock['symbol']
        query  = stock['news_query']
        print(f'  처리중: {name} ({symbol})')

        change_pct, open_p, close_p = get_weekly_change(symbol, last_monday, last_friday)
        headlines = get_stock_news(query, days=7)

        if change_pct is not None:
            causes, confidence, conf_reason = analyze_cause(name, symbol, change_pct, headlines)
        else:
            causes, confidence, conf_reason = ['주가 데이터 없음'], 0, ''

        results.append({
            'name': name, 'symbol': symbol,
            'change_pct': change_pct, 'open': open_p, 'close': close_p,
            'causes': causes, 'confidence': confidence, 'conf_reason': conf_reason,
        })

    msg = format_weekly_report(results, week_str)
    send_telegram(msg)
    print('✅ 주간 리포트 전송 완료!')


if __name__ == '__main__':
    run_weekly_report()
