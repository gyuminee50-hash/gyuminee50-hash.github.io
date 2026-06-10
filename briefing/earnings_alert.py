"""
실적 발표 D-2 사전 알림
포트폴리오 6종목 + 주요 관련 종목 어닝 일정 체크
매일 07:00 실행 → 오늘 기준 D-2 / D-1 / 당일 발표 종목 알림
"""
import json, os, requests
from datetime import datetime, timedelta

import yfinance as yf
import groq_client

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
with open(os.path.join(BASE_DIR, 'config.json'), 'r', encoding='utf-8') as f:
    _cfg = json.load(f)

# 모니터링 종목 (포트폴리오 개별주 + 관련 영향주, ETF 제외)
# SPY·QQQ·GGLL은 ETF/레버리지ETF → 실적 발표 없음
WATCHLIST = {
    # 포트폴리오 개별주
    'TSM':  'TSMC',
    'MU':   'Micron',
    'GOOGL':'Alphabet',
    # 포트폴리오 영향 종목
    'NVDA': 'NVIDIA',
    'AMD':  'AMD',
    'INTC': 'Intel',
    'AVGO': 'Broadcom',
    'MSFT': 'Microsoft',
    'AAPL': 'Apple',
    'AMZN': 'Amazon',
    'META': 'Meta',
    'TSLA': 'Tesla',
    'JPM':  'JPMorgan',
}


def send_telegram(text):
    token   = _cfg['telegram_token']
    chat_id = _cfg['telegram_chat_id']
    url     = f'https://api.telegram.org/bot{token}/sendMessage'
    requests.post(url,
        json={'chat_id': chat_id, 'text': text, 'parse_mode': 'HTML'},
        timeout=15)


# ── 어닝 데이터 수집 ─────────────────────────────────────────────────
def get_earnings_info(ticker):
    """
    yfinance에서 다음 실적 발표일 + 예상 EPS/매출 반환
    Returns: {'date': date, 'eps_est': float|None, 'rev_est': float|None}
    """
    try:
        tk = yf.Ticker(ticker)
        cal = tk.calendar

        # calendar는 dict 또는 DataFrame일 수 있음
        if cal is None:
            return None

        # dict 형태 처리
        if isinstance(cal, dict):
            earnings_date = cal.get('Earnings Date')
            eps_est       = cal.get('EPS Estimate')
            rev_est       = cal.get('Revenue Estimate')
        else:
            # DataFrame 형태
            try:
                earnings_date = cal.loc['Earnings Date'].values[0] if 'Earnings Date' in cal.index else None
                eps_est       = cal.loc['EPS Estimate'].values[0]  if 'EPS Estimate'  in cal.index else None
                rev_est       = cal.loc['Revenue Estimate'].values[0] if 'Revenue Estimate' in cal.index else None
            except Exception:
                return None

        if earnings_date is None:
            return None

        # 날짜 정규화
        if hasattr(earnings_date, '__iter__') and not isinstance(earnings_date, str):
            earnings_date = list(earnings_date)[0]
        if hasattr(earnings_date, 'date'):
            earnings_date = earnings_date.date()
        elif isinstance(earnings_date, str):
            earnings_date = datetime.strptime(earnings_date[:10], '%Y-%m-%d').date()

        return {
            'date':    earnings_date,
            'eps_est': eps_est,
            'rev_est': rev_est,
        }
    except Exception as e:
        print(f'    [캘린더 오류 {ticker}] {e}')
        return None


def get_recent_price_change(ticker):
    """최근 5일 주가 변동률"""
    try:
        tk   = yf.Ticker(ticker)
        hist = tk.history(period='6d')
        if len(hist) < 2:
            return None
        chg = (hist['Close'].iloc[-1] - hist['Close'].iloc[0]) / hist['Close'].iloc[0] * 100
        return round(chg, 2)
    except Exception:
        return None


# ── LLM 어닝 프리뷰 ──────────────────────────────────────────────────
_PREVIEW_PROMPT = """\
{name}({ticker}) 실적 발표가 {days}일 후다.
예상 EPS: {eps} / 예상 매출: {rev}

한국어로 투자자 관점 핵심 체크포인트 2가지를 각 25자 이내로 써줘.
번호 형식(1. / 2.)으로만, 설명 없이. 한자 금지."""


def _get_preview(ticker, name, days, eps_est, rev_est):
    eps_str = f'${eps_est:.2f}' if eps_est else '미공개'
    rev_str = f'${rev_est/1e9:.1f}B' if rev_est else '미공개'
    try:
        return groq_client.call(_PREVIEW_PROMPT.format(
            name=name, ticker=ticker, days=days,
            eps=eps_str, rev=rev_str))
    except Exception:
        return '1. 예상치 부합 여부 확인\n2. 가이던스 방향성 주목'


# ── D-day 라벨 ──────────────────────────────────────────────────────
def _day_label(days_until):
    if days_until == 0:
        return '🔴 오늘 발표', 0
    elif days_until == 1:
        return '🟠 내일 발표 (D-1)', 1
    elif days_until == 2:
        return '🟡 모레 발표 (D-2)', 2
    return None, days_until


# ── 메시지 포맷 ──────────────────────────────────────────────────────
def format_earnings_alert(alerts):
    today_str = datetime.now().strftime('%m/%d')
    lines = [f'<b>📅 실적 발표 알림  {today_str}</b>\n']

    for a in alerts:
        label, _ = _day_label(a['days_until'])
        eps_str  = f'${a["eps_est"]:.2f}' if a['eps_est'] else '미공개'
        rev_str  = f'${a["rev_est"]/1e9:.1f}B' if a['rev_est'] else '미공개'
        price_str = (f'  최근 5일 {a["price_chg"]:+.1f}%'
                     if a['price_chg'] is not None else '')

        lines.append(f'{label}')
        lines.append(f'<b>{a["name"]} ({a["ticker"]})</b>{price_str}')
        lines.append(f'예상 EPS {eps_str}  /  예상 매출 {rev_str}')

        if a.get('preview'):
            for line in a['preview'].splitlines():
                line = line.strip()
                if line:
                    lines.append(f'  • {line.lstrip("12. ")}')

        lines.append(f'  📆 발표일: {a["date"]}')
        lines.append('')

    lines.append('<i>* AP팀 — 발표 전 포지션 검토 권장</i>')
    return '\n'.join(lines)


# ── 메인 ────────────────────────────────────────────────────────────
def run_earnings_check():
    today  = datetime.now().date()
    alerts = []

    print(f'[{datetime.now().strftime("%Y-%m-%d %H:%M")}] 실적 발표 일정 체크...')

    for ticker, name in WATCHLIST.items():
        print(f'  확인중: {ticker}')
        info = get_earnings_info(ticker)
        if not info:
            continue

        days_until = (info['date'] - today).days
        if 0 <= days_until <= 2:
            price_chg = get_recent_price_change(ticker)
            preview   = _get_preview(ticker, name, days_until,
                                     info['eps_est'], info['rev_est'])
            alerts.append({
                'ticker':    ticker,
                'name':      name,
                'date':      info['date'],
                'days_until': days_until,
                'eps_est':   info['eps_est'],
                'rev_est':   info['rev_est'],
                'price_chg': price_chg,
                'preview':   preview,
            })

    if not alerts:
        print('  D-2 이내 발표 없음')
        return

    # 날짜 가까운 순 정렬
    alerts.sort(key=lambda x: x['days_until'])
    msg = format_earnings_alert(alerts)
    send_telegram(msg)
    print(f'  {len(alerts)}개 알림 전송 완료')


if __name__ == '__main__':
    run_earnings_check()
