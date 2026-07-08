"""
시장 이상 거래량 스캐너 — 고위험 신호 포착
기준: 거래량 3x 초과 + 주가 5% 이상 급등락
대상: S&P 500 전종목 (Wikipedia 자동 갱신, ETF 제외)
주 1~2개 수준의 진짜 신호만 전송 / 미국 장 중 매 시간 실행
"""
import json, os, sys, requests, sqlite3
from datetime import datetime, timezone, timedelta, date

import pandas as pd
import yfinance as yf
import groq_client

BASE_DIR       = os.path.dirname(os.path.abspath(__file__))
LOG_FILE       = os.path.join(BASE_DIR, 'scanner_log.json')
UNIVERSE_CACHE = os.path.join(BASE_DIR, 'sp500_cache.json')

with open(os.path.join(BASE_DIR, 'config.json'), 'r', encoding='utf-8') as f:
    _cfg = json.load(f)

def _log_signal_db(ticker, price_chg, vol_ratio, price, headline, signal_info):
    """signals 테이블에 이상거래량 신호 기록."""
    try:
        from db_setup import DB_PATH, init_db
        if not os.path.exists(DB_PATH):
            init_db()
        conn = sqlite3.connect(DB_PATH)
        reasoning = signal_info.get('판단', '') if signal_info else ''
        headline_str = signal_info.get('신호내용', f'{ticker} {price_chg:+.1f}%') if signal_info else f'{ticker} {price_chg:+.1f}%'
        conn.execute(
            """INSERT INTO signals
               (flagged_at,ticker,market,signal_type,headline,source,reasoning,rubric_score,groq_model,price_at_flag,benchmark)
               VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
            (datetime.now().isoformat(), ticker, 'US', '이상거래량',
             headline_str, 'VolumeScanner', reasoning, None,
             groq_client.MODEL, price, 'SPY')
        )
        conn.commit()
        conn.close()
    except Exception as e:
        print(f'  [스캐너 DB 로깅 오류] {e}')


# ── 스캔 유니버스: S&P 500 + 나스닥 100 (당일 캐시) ──────────────────
_NDX100_FALLBACK = [
    'AAPL','MSFT','NVDA','AMZN','META','GOOGL','GOOG','TSLA','AVGO','COST',
    'NFLX','ASML','AMD','INTC','QCOM','INTU','AMAT','CSCO','TXN','MRVL',
    'ADBE','MU','LRCX','PANW','KLAC','ADI','SNPS','CDNS','MELI','ISRG',
    'REGN','GILD','VRTX','MDLZ','BKNG','ADP','SBUX','PYPL','ABNB','CRWD',
    'DXCM','BIIB','IDXX','EA','FAST','GEHC','ODFL','CSGP','ON','TEAM',
    'DLTR','VRSK','ANSS','ZS','PCAR','ROST','CPRT','NXPI','MNST','FTNT',
    'MCHP','SIRI','ILMN','WBD','PARA','DDOG','SGEN','LCID','WDAY','OKTA',
]

_SP500_FALLBACK = [
    'AAPL','MSFT','NVDA','AMZN','GOOGL','META','TSLA','AVGO','BRK-B',
    'JPM','V','MA','LLY','XOM','UNH','JNJ','PG','HD','MRK','ABBV',
    'TSM','MU','INTC','AMD','QCOM','ARM','AMAT','LRCX','KLAC','MRVL',
    'GS','BAC','MS','WFC','AXP','BLK','C','SCHW','COF','USB',
    'CVX','COP','NEE','SLB','OXY','PSX','MPC','VLO','EOG','PXD',
    'WMT','COST','TGT','HD','LOW','NKE','MCD','SBUX','TJX','ROST',
    'NFLX','DIS','CMCSA','T','VZ','CHTR','EA','TTWO','WBD',
    'CAT','HON','DE','RTX','LMT','GE','UPS','BA','MMM','EMR',
    'CRM','NOW','ADBE','ORCL','INTU','SNOW','PLTR','UBER','COIN',
]


def get_universe():
    """S&P 500 + 나스닥 100 합집합 (중복 제거). 당일 캐시 사용."""
    today = date.today().isoformat()
    try:
        with open(UNIVERSE_CACHE, 'r') as f:
            cache = json.load(f)
        if cache.get('date') == today:
            return cache['tickers']
    except Exception:
        pass

    tickers = set()

    # S&P 500
    try:
        df = pd.read_html('https://en.wikipedia.org/wiki/List_of_S%26P_500_companies')[0]
        sp500 = df['Symbol'].str.replace('.', '-', regex=False).tolist()
        tickers.update(sp500)
        print(f'  S&P 500: {len(sp500)}개')
    except Exception as e:
        print(f'  [S&P500 오류] {e} → fallback 사용')
        tickers.update(_SP500_FALLBACK)

    # 나스닥 100
    try:
        tables = pd.read_html('https://en.wikipedia.org/wiki/Nasdaq-100')
        ndx_df = next(t for t in tables if 'Ticker' in t.columns or 'Symbol' in t.columns)
        col = 'Ticker' if 'Ticker' in ndx_df.columns else 'Symbol'
        ndx100 = ndx_df[col].str.replace('.', '-', regex=False).tolist()
        tickers.update(ndx100)
        print(f'  나스닥 100: {len(ndx100)}개')
    except Exception as e:
        print(f'  [나스닥100 오류] {e} → fallback 사용')
        tickers.update(_NDX100_FALLBACK)

    result = sorted(tickers)
    with open(UNIVERSE_CACHE, 'w') as f:
        json.dump({'date': today, 'tickers': result}, f)
    print(f'  유니버스 확정: {len(result)}개 (S&P500 + 나스닥100 합산)')
    return result

# ── 알림 중복 방지 로그 ──────────────────────────────────────────────
def _load_log():
    try:
        with open(LOG_FILE, 'r', encoding='utf-8') as f:
            log = json.load(f)
        # 날짜가 다르면 초기화
        if log.get('date') != date.today().isoformat():
            return {'date': date.today().isoformat(), 'alerted': []}
        return log
    except Exception:
        return {'date': date.today().isoformat(), 'alerted': []}

def _save_log(log):
    with open(LOG_FILE, 'w', encoding='utf-8') as f:
        json.dump(log, f)

# ── 유틸 ────────────────────────────────────────────────────────────
def _is_market_hours():
    # pytz 없이 정확한 ET 처리: 미국 동부 DST는 3월 둘째 일~11월 첫째 일
    utc_now = datetime.now(timezone.utc)
    # DST 적용 여부: 3/8 ~ 11/1 사이면 EDT(UTC-4), 아니면 EST(UTC-5)
    y = utc_now.year
    dst_start = datetime(y, 3,  8, 2, tzinfo=timezone.utc) + timedelta(days=(6 - datetime(y, 3, 8).weekday()) % 7)
    dst_end   = datetime(y, 11, 1, 2, tzinfo=timezone.utc) + timedelta(days=(6 - datetime(y, 11,1).weekday()) % 7)
    offset = timedelta(hours=-4) if dst_start <= utc_now < dst_end else timedelta(hours=-5)
    et = utc_now.astimezone(timezone(offset))
    return et.weekday() < 5 and 9 <= et.hour < 16

def send_telegram(text):
    token   = _cfg['telegram_token']
    chat_id = _cfg['telegram_chat_id']
    url     = f'https://api.telegram.org/bot{token}/sendMessage'
    requests.post(url,
        json={'chat_id': chat_id, 'text': text, 'parse_mode': 'HTML'},
        timeout=15)

# ── 종목 뉴스 수집 + Groq 선행신호 판단 ────────────────────────────────
import xml.etree.ElementTree as _ET

def _fetch_ticker_news(ticker, max_items=4):
    """Google News RSS → 종목 최신 헤드라인"""
    url = (f'https://news.google.com/rss/search?q={ticker}+stock'
           f'&hl=en-US&gl=US&ceid=US:en')
    try:
        resp  = requests.get(url, timeout=8, headers={'User-Agent': 'Mozilla/5.0'})
        items = _ET.fromstring(resp.content).findall('.//item')
        return ' / '.join(
            it.find('title').text.strip() for it in items[:max_items]
            if it.find('title') is not None
        )
    except Exception:
        return ''

_DROP_CHECK_PROMPT = """\
종목: {ticker}
오늘 {chg:+.1f}% 급락, 거래량 평소의 {vol:.1f}배
최신 뉴스 헤드라인: {news}

이 급락의 성격을 판단하라.
아래 형식으로만 응답. 다른 텍스트 없이:

급락성격: 일시적 / 구조적 / 불명확
매수판단: 강매수검토 / 매수검토 / 모니터링 / 회피
이유: (30자 이내)
뉴스출처: (있을 때만)

원칙:
- 일시적: 단기 이슈·과매도·비펀더멘털 원인 → 강매수검토/매수검토
- 불명확: 원인 파악 어려움 → 모니터링
- 구조적: 비즈니스 훼손·규제·재무위험 → 회피
- 확신 없으면 불명확 + 모니터링"""

_SURGE_CHECK_PROMPT = """\
종목: {ticker}
오늘 {chg:+.1f}% 급등, 거래량 평소의 {vol:.1f}배
최신 뉴스 헤드라인: {news}

이 급등에 "시장에 아직 미반영된 선행신호"가 있는가?
아래 형식으로만 응답. 다른 텍스트 없이:

선행신호: 있음 / 없음
신호내용: (있을 때만 — 30자 이내)
뉴스출처: (있을 때만)
판단: 한 줄

원칙: 확신 없으면 없음. 0건 정상."""

def _groq_signal_check(ticker, price_chg, vol_ratio):
    """Groq 판단 요청. 급락: 매수 판단, 급등: 선행신호 판단. (has_signal, info_dict) 반환."""
    news = _fetch_ticker_news(ticker)
    if not news:
        return False, {}
    try:
        if price_chg < 0:
            raw = groq_client.call(
                _DROP_CHECK_PROMPT.format(ticker=ticker, chg=price_chg, vol=vol_ratio, news=news),
                max_tokens=180, temperature=0.2,
            )
            lines = {l.split(':')[0].strip(): ':'.join(l.split(':')[1:]).strip()
                     for l in raw.strip().splitlines() if ':' in l}
            lines['_type'] = 'drop'
            # 회피만 제외, 나머지는 통과 (강매수검토/매수검토/모니터링)
            has = '회피' not in lines.get('매수판단', '회피')
            return has, lines
        else:
            raw = groq_client.call(
                _SURGE_CHECK_PROMPT.format(ticker=ticker, chg=price_chg, vol=vol_ratio, news=news),
                max_tokens=180, temperature=0.2,
            )
            lines = {l.split(':')[0].strip(): ':'.join(l.split(':')[1:]).strip()
                     for l in raw.strip().splitlines() if ':' in l}
            lines['_type'] = 'surge'
            has = '있음' in lines.get('선행신호', '없음')
            return has, lines
    except Exception as e:
        print(f'    [Groq 판단 오류 {ticker}] {e}')
        return False, {}


# ── 스캔 핵심 로직 ───────────────────────────────────────────────────
def scan_universe(vol_threshold=2.0, price_threshold=3.0):
    """
    거래량 2x 이상 + 주가 3% 이상 + Groq 선행신호 있음 (AND 3가지 동시 충족)
    0건도 정상 — GM Capital 핵심 철학
    """
    already_alerted = _load_log()['alerted']
    universe = get_universe()

    print(f'  [{datetime.now().strftime("%H:%M")}] S&P500 {len(universe)}개 종목 스캔 중...')
    try:
        raw = yf.download(
            universe,
            period='25d',
            interval='1d',
            group_by='ticker',
            auto_adjust=True,
            progress=False,
            threads=True,
        )
    except Exception as e:
        print(f'  [다운로드 오류] {e}')
        return []

    signals = []
    for ticker in universe:
        if ticker in already_alerted:
            continue  # 오늘 이미 알린 종목 skip
        try:
            if ticker not in raw.columns.get_level_values(0):
                continue
            df = raw[ticker].dropna()
            if len(df) < 6:
                continue

            avg_vol    = df['Volume'].iloc[:-1].tail(20).mean()
            today_vol  = df['Volume'].iloc[-1]
            if avg_vol < 500_000 or today_vol == 0:  # 소형주 제외
                continue

            vol_ratio  = today_vol / avg_vol
            prev_close = df['Close'].iloc[-2]
            today_close= df['Close'].iloc[-1]
            price_chg  = (today_close - prev_close) / prev_close * 100

            if vol_ratio >= vol_threshold and abs(price_chg) >= price_threshold:
                print(f'    [{ticker}] {price_chg:+.1f}% / {vol_ratio:.1f}x → Groq 선행신호 판단 중...')
                has_signal, signal_info = _groq_signal_check(ticker, price_chg, vol_ratio)
                if has_signal:
                    signals.append({
                        'ticker':      ticker,
                        'price_chg':   round(price_chg, 2),
                        'vol_ratio':   round(vol_ratio, 1),
                        'today_vol':   int(today_vol),
                        'price':       round(today_close, 2),
                        'strength':    vol_ratio * abs(price_chg),
                        'signal_info': signal_info,
                    })
                    _log_signal_db(ticker, price_chg, vol_ratio,
                                   round(today_close, 2), None, signal_info)
                else:
                    print(f'    [{ticker}] Groq 선행신호 없음 — 제외')
        except Exception:
            continue

    signals.sort(key=lambda x: x['strength'], reverse=True)
    return signals[:3]  # 최대 3개 (진짜 강한 신호만)

# ── 신호 분류 ────────────────────────────────────────────────────────
def _signal_type(price_chg):
    if price_chg >= 10:  return '급등', '🚀'
    elif price_chg >= 5: return '상승 급증', '📈'
    elif price_chg <= -10: return '급락 경보', '🔴'
    else:                return '하락 급증', '📉'

# ── LLM 원인 추정 ────────────────────────────────────────────────────
_SIGNAL_PROMPT = """\
{ticker} 주식이 오늘 {chg:+.1f}% 급{direction}하면서 거래량이 평소의 {vol:.1f}배 터졌다.
투자자 관점에서 가능한 원인을 한국어 1~2문장으로 추정해줘.
"~가능성" "~추정" 어조 유지. 한자 금지. 40자 이내."""

def _explain(ticker, price_chg, vol_ratio):
    direction = '등' if price_chg > 0 else '락'
    try:
        return groq_client.call(_SIGNAL_PROMPT.format(
            ticker=ticker, chg=price_chg, vol=vol_ratio, direction=direction),
            max_tokens=150)
    except Exception:
        return '원인 분석 불가'

# ── 메시지 포맷 ─────────────────────────────────────────────────────
def format_alert(signals):
    now_str = datetime.now().strftime('%m/%d %H:%M')
    lines   = [f'<b>📡 이상거래량 신호  {now_str}</b>\n']

    for s in signals:
        _, icon = _signal_type(s['price_chg'])
        sign = '+' if s['price_chg'] >= 0 else ''
        info = s.get('signal_info', {})
        is_drop = s['price_chg'] < 0

        if is_drop:
            lines.append(f'<b>📉 급락 — 매수검토</b>')
            lines.append(
                f'{icon} <b>{s["ticker"]}</b>  '
                f'<b>{sign}{s["price_chg"]:.1f}%</b>  |  거래량 {s["vol_ratio"]:.1f}x'
            )
            if info.get('급락성격'):
                lines.append(f'성격: {info["급락성격"]}')
            if info.get('매수판단'):
                lines.append(f'판단: <b>{info["매수판단"]}</b>')
            if info.get('이유'):
                lines.append(f'이유: {info["이유"]}')
            if info.get('뉴스출처'):
                lines.append(f'출처: {info["뉴스출처"]}')
        else:
            lines.append(f'<b>🚀 급등 — 선행신호</b>')
            lines.append(
                f'{icon} <b>{s["ticker"]}</b>  '
                f'<b>{sign}{s["price_chg"]:.1f}%</b>  |  거래량 {s["vol_ratio"]:.1f}x'
            )
            if info.get('신호내용'):
                lines.append(f'신호: {info["신호내용"]}')
            if info.get('뉴스출처'):
                lines.append(f'출처: {info["뉴스출처"]}')
            if info.get('판단'):
                lines.append(f'판단: {info["판단"]}')

        lines.append(f'현재가 ${s["price"]}')
        lines.append('')

    lines.append('<i>* 급락: 회피 제외(강매수검토/매수검토/모니터링 전송)</i>')
    lines.append('<i>  급등: Groq 선행신호 있음만 전송 / 0건 정상</i>')
    return '\n'.join(lines)

# ── 메인 ────────────────────────────────────────────────────────────
def run_scan(force=False):
    if not force and not _is_market_hours():
        print('  장외 시간 — 스캔 생략')
        return

    print(f'[{datetime.now().strftime("%Y-%m-%d %H:%M")}] 고위험 신호 스캔 시작...')
    signals = scan_universe()

    if not signals:
        print('  기준 미달 — 전송 없음')
        return

    # 알림 로그 업데이트 (오늘 이미 알린 종목 기록)
    log = _load_log()
    log['alerted'].extend(s['ticker'] for s in signals)
    _save_log(log)

    print(f'  신호 {len(signals)}개 감지 → 텔레그램 전송')
    msg = format_alert(signals)
    send_telegram(msg)
    print('  전송 완료')


if __name__ == '__main__':
    force = '--force' in sys.argv
    run_scan(force=force)
