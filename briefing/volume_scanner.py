"""
시장 이상 거래량 스캐너 — 고위험 신호 포착
기준: 거래량 3x 초과 + 주가 5% 이상 급등락
대상: S&P 500 전종목 (Wikipedia 자동 갱신, ETF 제외)
주 1~2개 수준의 진짜 신호만 전송 / 미국 장 중 매 시간 실행
"""
import json, os, sys, requests
from datetime import datetime, timezone, timedelta, date

import pandas as pd
import yfinance as yf
import groq_client

BASE_DIR       = os.path.dirname(os.path.abspath(__file__))
LOG_FILE       = os.path.join(BASE_DIR, 'scanner_log.json')
UNIVERSE_CACHE = os.path.join(BASE_DIR, 'sp500_cache.json')

with open(os.path.join(BASE_DIR, 'config.json'), 'r', encoding='utf-8') as f:
    _cfg = json.load(f)


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

# ── 스캔 핵심 로직 ───────────────────────────────────────────────────
def scan_universe(vol_threshold=3.0, price_threshold=5.0):
    """
    거래량 3x 초과 + 주가 변동 5% 초과 → 진짜 고위험 신호만
    주 1~2개 수준이 목표
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
                signals.append({
                    'ticker':    ticker,
                    'price_chg': round(price_chg, 2),
                    'vol_ratio': round(vol_ratio, 1),
                    'today_vol': int(today_vol),
                    'price':     round(today_close, 2),
                    'strength':  vol_ratio * abs(price_chg),
                })
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
    lines   = [f'<b>⚡ 고위험 신호 포착  {now_str}</b>\n']

    for s in signals:
        label, icon = _signal_type(s['price_chg'])
        sign  = '+' if s['price_chg'] >= 0 else ''
        vol_m = s['today_vol'] / 1_000_000

        lines.append(
            f'{icon} <b>{s["ticker"]}</b>  '
            f'<b>{sign}{s["price_chg"]:.1f}%</b>  |  '
            f'거래량 {s["vol_ratio"]:.1f}x ({vol_m:.1f}M주)'
        )
        explanation = _explain(s['ticker'], s['price_chg'], s['vol_ratio'])
        lines.append(f'   <i>{explanation}</i>')
        lines.append(f'   현재가 ${s["price"]}')
        lines.append('')

    lines.append('<i>* 기준: 거래량 3x 초과 + 주가 5% 이상 급등락 (개별 주식)</i>')
    lines.append('<i>  고위험 신호 — AP팀 판단 후 활용 권장</i>')
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
