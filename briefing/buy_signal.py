"""
GM Capital 사전 분할매수 신호 — AP팀
S&P500+NDX100 전종목 중 과매도 구간 진입 종목 탐지
기준: 20일 고점 대비 -15%↓ + RSI < 38 + Groq 반등 가능성 보통 이상
매일 22:00 실행 (미국 장 개시 직전)
0건도 정상 — GM Capital 원칙
"""
import json, os, sys, requests
import yfinance as yf
import yaml
import xml.etree.ElementTree as _ET
from datetime import datetime, date

sys.stdout.reconfigure(encoding='utf-8')

import groq_client

BASE_DIR       = os.path.dirname(os.path.abspath(__file__))
UNIVERSE_CACHE = os.path.join(BASE_DIR, 'sp500_cache.json')
LOG_FILE       = os.path.join(BASE_DIR, 'buy_signal_log.json')

with open(os.path.join(BASE_DIR, 'config.json'), 'r', encoding='utf-8') as f:
    _cfg = json.load(f)
with open(os.path.join(BASE_DIR, 'config.yaml'), 'r', encoding='utf-8') as f:
    _ycfg = yaml.safe_load(f)

EXCLUDE = set(_ycfg['universe']['exclude_holdings'])  # 보유종목 제외

# ── 상수 ──────────────────────────────────────────────────────────────
DRAWDOWN_THRESHOLD = -15.0   # 20일 고점 대비 낙폭
RSI_THRESHOLD      = 38.0    # 과매도 진입 기준
MAX_ALERTS         = 3       # 하루 최대 발송 건수


# ── 유틸 ──────────────────────────────────────────────────────────────
def _rsi(series, period=14):
    delta = series.diff()
    gain  = delta.clip(lower=0).rolling(period).mean()
    loss  = (-delta.clip(upper=0)).rolling(period).mean()
    rs    = gain / loss
    return 100 - (100 / (1 + rs))

def _load_log():
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

def _get_universe():
    """volume_scanner 캐시 재사용. 없으면 fallback."""
    _SP500_FALLBACK = [
        'AAPL','MSFT','NVDA','AMZN','GOOGL','META','TSLA','AVGO','BRK-B',
        'JPM','V','MA','LLY','XOM','UNH','JNJ','PG','HD','MRK','ABBV',
        'TSM','MU','INTC','AMD','QCOM','ARM','AMAT','LRCX','KLAC','MRVL',
        'GS','BAC','MS','WFC','AXP','BLK','C','SCHW','COF','USB',
        'CVX','COP','NEE','SLB','OXY','PSX','MPC','VLO','EOG',
        'WMT','COST','TGT','LOW','NKE','MCD','SBUX','TJX','ROST',
        'NFLX','DIS','CMCSA','T','VZ','CHTR','EA','TTWO',
        'CAT','HON','DE','RTX','LMT','GE','UPS','BA','MMM','EMR',
        'CRM','NOW','ADBE','ORCL','INTU','SNOW','PLTR','UBER','COIN',
    ]
    try:
        with open(UNIVERSE_CACHE, 'r') as f:
            cache = json.load(f)
        return cache['tickers']
    except Exception:
        print('  [캐시 없음] fallback 500개 사용')
        return _SP500_FALLBACK

def _fetch_news(ticker, max_items=3):
    url = (f'https://news.google.com/rss/search?q={ticker}+stock'
           f'&hl=en-US&gl=US&ceid=US:en')
    try:
        resp  = requests.get(url, timeout=8, headers={'User-Agent': 'Mozilla/5.0'})
        items = _ET.fromstring(resp.content).findall('.//item')
        return ' / '.join(
            it.find('title').text.strip()
            for it in items[:max_items]
            if it.find('title') is not None
        )
    except Exception:
        return ''


# ── Groq 반등 가능성 판단 ─────────────────────────────────────────────
_BUY_PROMPT = """\
종목: {ticker}
20일 고점 대비: {drawdown:.1f}% 하락
RSI: {rsi:.0f} (과매도 구간)
최신 뉴스: {news}

이 종목이 분할매수 진입 구간인가?
아래 형식으로만:

반등가능성: 높음 / 보통 / 낮음
성격: 일시적조정 / 구조적하락 / 불명확
1차매수: X% 비중 — 타이밍 조건 (20자 이내)
추가매수: 조건 (없으면 생략)
주의: 한 줄

원칙:
- 구조적 하락(비즈니스 훼손·파산·규제) → 낮음
- 일시적 조정·과매도·섹터 로테이션 → 보통 이상
- 확신 없으면 낮음"""


# ── 스캔 핵심 ────────────────────────────────────────────────────────
def scan_oversold():
    log      = _load_log()
    universe = _get_universe()
    targets  = [t for t in universe if t not in EXCLUDE and t not in log['alerted']]
    print(f'  {len(targets)}개 종목 과매도 스캔...')

    try:
        raw   = yf.download(targets, period='30d', interval='1d',
                            auto_adjust=True, progress=False, threads=True)
        close = raw['Close']
    except Exception as e:
        print(f'  [다운로드 오류] {e}')
        return [], log

    # 필터링: 낙폭 + RSI
    candidates = []
    for ticker in targets:
        try:
            col = close[ticker].dropna() if ticker in close.columns else None
            if col is None or len(col) < 15:
                continue
            cur      = float(col.iloc[-1])
            high20   = float(col.tail(20).max())
            drawdown = (cur - high20) / high20 * 100
            if drawdown > DRAWDOWN_THRESHOLD:
                continue
            rsi_val = float(_rsi(col).iloc[-1])
            if rsi_val > RSI_THRESHOLD:
                continue
            candidates.append({
                'ticker':   ticker,
                'price':    round(cur, 2),
                'drawdown': round(drawdown, 1),
                'rsi':      round(rsi_val, 1),
            })
        except Exception:
            continue

    # RSI 낮은 순 (가장 과매도)
    candidates.sort(key=lambda x: x['rsi'])
    print(f'  필터 통과: {len(candidates)}개 (낙폭≤{DRAWDOWN_THRESHOLD}% + RSI≤{RSI_THRESHOLD})')

    signals = []
    for c in candidates[:8]:   # Groq 최대 8개 확인
        ticker = c['ticker']
        news   = _fetch_news(ticker)
        if not news:
            continue
        try:
            raw_g   = groq_client.call(
                _BUY_PROMPT.format(ticker=ticker, drawdown=c['drawdown'],
                                   rsi=c['rsi'], news=news),
                max_tokens=200, temperature=0.2,
            )
            lines_d = {l.split(':')[0].strip(): ':'.join(l.split(':')[1:]).strip()
                       for l in raw_g.strip().splitlines() if ':' in l}
            poss = lines_d.get('반등가능성', '낮음')
            if poss in ('높음', '보통'):
                signals.append({**c, 'groq': raw_g, 'possibility': poss})
                log['alerted'].append(ticker)
                print(f'  [{ticker}] 반등가능성 {poss} ✓')
            else:
                print(f'  [{ticker}] 반등가능성 낮음 — 제외')
        except Exception as e:
            print(f'  [{ticker}] Groq 오류: {e}')

        if len(signals) >= MAX_ALERTS:
            break

    return signals, log


# ── 텔레그램 ─────────────────────────────────────────────────────────
def _send(text):
    requests.post(
        f"https://api.telegram.org/bot{_cfg['telegram_token']}/sendMessage",
        json={'chat_id': _cfg['telegram_chat_id'], 'text': text, 'parse_mode': 'HTML'},
        timeout=15,
    )


def run_buy_signal():
    print(f'[{datetime.now().strftime("%Y-%m-%d %H:%M")}] 사전 분할매수 신호 스캔...')
    signals, log = scan_oversold()
    _save_log(log)

    if not signals:
        print('  분할매수 진입 구간 없음 (0건 정상)')
        return

    now_str = datetime.now().strftime('%m/%d %H:%M')
    lines   = [f'<b>🟢 분할매수 진입 구간  {now_str}</b>', '']

    for s in signals:
        icon = '🔥' if s['possibility'] == '높음' else '📊'
        lines += [
            f'{icon} <b>{s["ticker"]}  ${s["price"]}</b>',
            f'20일 낙폭 {s["drawdown"]:.1f}%  |  RSI {s["rsi"]:.0f}',
            s['groq'].strip(),
            '',
        ]

    lines += [
        f'<i>* 기준: 20일 고점 {DRAWDOWN_THRESHOLD}%↓ + RSI {RSI_THRESHOLD} 이하 + Groq 반등가능성</i>',
        '<i>  0건 정상 — GM Capital 원칙</i>',
    ]
    _send('\n'.join(lines))
    print(f'✅ 분할매수 신호 {len(signals)}건 발송!')


if __name__ == '__main__':
    run_buy_signal()
