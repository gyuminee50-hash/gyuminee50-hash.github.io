"""
시장 이상 거래량 스캐너 — 고위험 신호 포착
기준: 거래량 3x 초과 + 주가 5% 이상 급등락 (개별 주식만)
ETF 제외, 주 1~2개 수준의 진짜 신호만 전송
미국 장 중(KST 23:30~06:00) 매 시간 실행
"""
import json, os, sys, requests
from datetime import datetime, timezone, timedelta, date

import yfinance as yf
import groq_client

BASE_DIR  = os.path.dirname(os.path.abspath(__file__))
LOG_FILE  = os.path.join(BASE_DIR, 'scanner_log.json')   # 오늘 이미 알린 종목 추적

with open(os.path.join(BASE_DIR, 'config.json'), 'r', encoding='utf-8') as f:
    _cfg = json.load(f)

# ── 스캔 대상: 개별 주식만 (ETF 전부 제외) ─────────────────────────
UNIVERSE = [
    # 빅테크
    'AAPL','MSFT','NVDA','AMZN','GOOGL','META','TSLA','AVGO','ORCL','ADBE',
    # 반도체 (포트폴리오 직결)
    'TSM','MU','INTC','AMD','QCOM','ARM','AMAT','LRCX','KLAC','MRVL','SMCI',
    # 금융
    'JPM','BAC','GS','MS','WFC','V','MA','AXP','BLK',
    # 헬스케어
    'LLY','UNH','JNJ','ABBV','MRK','PFE','TMO','DHR',
    # 에너지·산업
    'XOM','CVX','COP','CAT','HON','DE','RTX','LMT','GE','NEE',
    # 소비재·유통
    'WMT','COST','HD','NKE','MCD','SBUX','TGT','LOW',
    # 통신·미디어
    'NFLX','DIS','CMCSA','T','VZ',
    # 기타 대형주
    'BRK-B','CRM','NOW','UBER','COIN','PLTR',
]

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
    et = datetime.now(timezone(timedelta(hours=-4)))  # EDT
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

    print(f'  [{datetime.now().strftime("%H:%M")}] {len(UNIVERSE)}개 종목 스캔 중...')
    try:
        raw = yf.download(
            UNIVERSE,
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
    for ticker in UNIVERSE:
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
