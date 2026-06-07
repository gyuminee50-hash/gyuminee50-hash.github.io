"""
시장 전체 이상 거래량 스캐너
S&P 500 + Nasdaq 100 대상, 거래량 2.5x 초과 + 주가 변동 2% 이상 종목 포착
미국 장 중(KST 23:30~06:00) 매 시간 실행, 신호 있을 때만 텔레그램 전송
"""
import json, os, sys, requests
from datetime import datetime, timezone, timedelta

import pandas as pd
import yfinance as yf
import groq_client

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
with open(os.path.join(BASE_DIR, 'config.json'), 'r', encoding='utf-8') as f:
    _cfg = json.load(f)

# ── 스캔 대상 종목 (S&P 100 + 주요 Nasdaq) ──────────────────────────
UNIVERSE = [
    # 빅테크
    'AAPL','MSFT','NVDA','AMZN','GOOGL','META','TSLA','AVGO','ORCL','ADBE',
    # 반도체
    'TSM','MU','INTC','AMD','QCOM','ARM','AMAT','LRCX','KLAC','MRVL','SMCI',
    # 금융
    'JPM','BAC','GS','MS','WFC','BRK-B','V','MA','AXP','BLK',
    # 헬스케어
    'LLY','UNH','JNJ','ABBV','MRK','PFE','TMO','DHR','ABT',
    # 에너지·산업
    'XOM','CVX','COP','NEE','CAT','HON','DE','RTX','LMT','GE',
    # 소비재·유통
    'WMT','COST','HD','NKE','MCD','SBUX','TGT','LOW','TJX',
    # 통신·미디어
    'NFLX','DIS','CMCSA','T','VZ',
    # ETF (섹터 신호)
    'XLK','XLF','XLE','XLV','SOXX','SMH','ARKK',
    # 포트폴리오 종목
    'GGLL','IEMG','SPY','QQQ',
]


# ── 유틸 ────────────────────────────────────────────────────────────
def _is_market_hours():
    """미국 동부 시간 09:00~16:30 체크 (서머타임 무관하게 대략 KST -13h)"""
    et = datetime.now(timezone(timedelta(hours=-4)))  # EDT (서머타임)
    return et.weekday() < 5 and 9 <= et.hour < 16




def send_telegram(text):
    token   = _cfg['telegram_token']
    chat_id = _cfg['telegram_chat_id']
    url     = f'https://api.telegram.org/bot{token}/sendMessage'
    requests.post(url,
        json={'chat_id': chat_id, 'text': text, 'parse_mode': 'HTML'},
        timeout=15)


# ── 스캔 핵심 로직 ───────────────────────────────────────────────────
def scan_universe(vol_threshold=2.5, price_threshold=2.0):
    """
    거래량 vol_threshold 배 초과 + 주가 변동 price_threshold% 초과 종목 탐색
    Returns: list of dicts sorted by signal strength
    """
    print(f'  [{datetime.now().strftime("%H:%M")}] {len(UNIVERSE)}개 종목 다운로드 중...')
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
        try:
            if ticker in raw.columns.get_level_values(0):
                df = raw[ticker].dropna()
            else:
                continue

            if len(df) < 6:
                continue

            # 최근 20일 평균 거래량 (오늘 제외)
            avg_vol   = df['Volume'].iloc[:-1].tail(20).mean()
            today_vol = df['Volume'].iloc[-1]
            if avg_vol < 100_000 or today_vol == 0:
                continue

            vol_ratio = today_vol / avg_vol

            # 오늘 가격 변동
            prev_close = df['Close'].iloc[-2]
            today_close = df['Close'].iloc[-1]
            price_chg  = (today_close - prev_close) / prev_close * 100

            if vol_ratio >= vol_threshold and abs(price_chg) >= price_threshold:
                signals.append({
                    'ticker':     ticker,
                    'price_chg':  round(price_chg, 2),
                    'vol_ratio':  round(vol_ratio, 1),
                    'today_vol':  int(today_vol),
                    'price':      round(today_close, 2),
                    'strength':   vol_ratio * abs(price_chg),  # 신호 강도 점수
                })
        except Exception:
            continue

    signals.sort(key=lambda x: x['strength'], reverse=True)
    return signals[:10]


# ── 신호 분류 ────────────────────────────────────────────────────────
def _signal_type(price_chg, vol_ratio):
    if price_chg >= 5:
        return '급등', '🚀'
    elif price_chg >= 2:
        return '상승 급증', '📈'
    elif price_chg <= -5:
        return '급락', '🔴'
    elif price_chg <= -2:
        return '하락 급증', '📉'
    return '의심 거래', '⚠️'


# ── LLM 간단 해설 ────────────────────────────────────────────────────
_SIGNAL_PROMPT = """\
{ticker} 주식이 오늘 {chg:+.1f}% 변동하면서 거래량이 평소 대비 {vol:.1f}배 터졌다.
한국어로 투자자 관점에서 가능한 원인을 1~2문장으로 추정해줘.
단정하지 말고 "~가능성" "~추정" 어조로. 한자 금지. 30자 이내."""


def _explain(ticker, price_chg, vol_ratio):
    try:
        return groq_client.call(_SIGNAL_PROMPT.format(
            ticker=ticker, chg=price_chg, vol=vol_ratio))
    except Exception:
        return '원인 분석 불가'


# ── 메시지 포맷 ───────────────────────────────────────────────────────
def format_alert(signals):
    now_str = datetime.now().strftime('%m/%d %H:%M')
    lines   = [f'<b>🔍 이상 거래량 신호  {now_str}</b>\n']

    for s in signals:
        label, icon = _signal_type(s['price_chg'], s['vol_ratio'])
        sign = '+' if s['price_chg'] >= 0 else ''
        vol_m = s['today_vol'] / 1_000_000

        lines.append(
            f'{icon} <b>{s["ticker"]}</b>  '
            f'{sign}{s["price_chg"]:.1f}%  |  '
            f'거래량 <b>{s["vol_ratio"]:.1f}x</b> ({vol_m:.1f}M)'
        )
        explanation = _explain(s['ticker'], s['price_chg'], s['vol_ratio'])
        lines.append(f'   <i>{explanation}</i>')
        lines.append('')

    lines.append('<i>* 거래량 2.5x + 변동 2% 이상 종목 자동 포착</i>')
    lines.append('<i>  고위험 신호 — AP팀 판단 후 활용 권장</i>')
    return '\n'.join(lines)


# ── 메인 ────────────────────────────────────────────────────────────
def run_scan(force=False):
    if not force and not _is_market_hours():
        print('  장외 시간 — 스캔 생략')
        return

    print(f'[{datetime.now().strftime("%Y-%m-%d %H:%M")}] 이상 거래량 스캔 시작...')
    signals = scan_universe()

    if not signals:
        print('  신호 없음 — 전송 생략')
        return

    print(f'  신호 {len(signals)}개 감지 -> 텔레그램 전송')
    msg = format_alert(signals)
    send_telegram(msg)
    print('  전송 완료')


if __name__ == '__main__':
    force = '--force' in sys.argv
    run_scan(force=force)
