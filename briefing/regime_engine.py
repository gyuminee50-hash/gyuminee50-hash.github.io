"""
시장 Regime 엔진 — AI 패밀리오피스 ④
==========================================
매일 22:30 실행 (미국 장 마감 후)
- yfinance로 VIX / 10년물 금리 / S&P500 / DXY 조회
- 4개 항목 점수화 (+2 ~ -2)
- 총점 → 5단계 국면 판단
- 국면별 자산배분 권고 텔레그램 발송
"""
import json, os, sys
import requests
import yfinance as yf
from datetime import datetime, timedelta

sys.stdout.reconfigure(encoding='utf-8')

import groq_client

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
with open(os.path.join(BASE_DIR, 'config.json'), 'r', encoding='utf-8') as f:
    _cfg = json.load(f)

# ── 국면 판단 기준표 ───────────────────────────────────────────────
REGIME_TABLE = [
    (+4,  99,  'Strong Risk-On',  '성장주 70% / 가치주 20% / 현금 10%'),
    (+1,  +3,  'Risk-On',         '성장주 60% / 가치주 20% / 현금 20%'),
    ( 0,   0,  '중립',             '성장주 40% / 가치주 30% / 현금 30%'),
    (-3,  -1,  'Risk-Off',        '성장주 20% / 가치주 40% / 현금 40%'),
    (-99, -4,  'Strong Risk-Off', '성장주 10% / 가치주 30% / 현금 60%'),
]

def _get_regime(score):
    for lo, hi, name, alloc in REGIME_TABLE:
        if lo <= score <= hi:
            return name, alloc
    return '중립', '성장주 40% / 가치주 30% / 현금 30%'

# ── 데이터 조회 ──────────────────────────────────────────────────
def _fetch(ticker, period='5d'):
    try:
        df = yf.download(ticker, period=period, interval='1d',
                         auto_adjust=True, progress=False)
        if df.empty:
            return None
        return float(df['Close'].iloc[-1])
    except Exception:
        return None

def _fetch_change_pct(ticker, days=90):
    """N일 전 대비 변화율 (유동성 프록시용)."""
    try:
        df = yf.download(ticker, period=f'{days+10}d', interval='1d',
                         auto_adjust=True, progress=False)
        if len(df) < 10:
            return None
        start = float(df['Close'].iloc[-days]) if len(df) >= days else float(df['Close'].iloc[0])
        end   = float(df['Close'].iloc[-1])
        return (end - start) / start * 100
    except Exception:
        return None

# ── 항목별 점수 계산 ──────────────────────────────────────────────
def _score_vix(vix):
    if vix is None:   return 0, 'N/A'
    if vix <= 15:     return +2, f'VIX {vix:.1f} ≤ 15 → 시장 안정'
    if vix <= 20:     return +1, f'VIX {vix:.1f} ≤ 20 → 비교적 안정'
    if vix <= 25:     return -1, f'VIX {vix:.1f} > 20 → 불안정'
    return            -2,        f'VIX {vix:.1f} > 25 → 고변동성'

def _score_rate(rate_10y):
    """10년물 금리. 방향성(전월 대비)으로 판단."""
    if rate_10y is None: return 0, 'N/A'
    if rate_10y < 3.5:   return +2, f'금리 {rate_10y:.2f}% — 완화적'
    if rate_10y < 4.5:   return +1, f'금리 {rate_10y:.2f}% — 보통'
    if rate_10y < 5.0:   return -1, f'금리 {rate_10y:.2f}% — 긴축 압박'
    return               -2,        f'금리 {rate_10y:.2f}% — 강긴축'

def _score_spy(spy_90d_chg):
    """S&P500 90일 모멘텀."""
    if spy_90d_chg is None: return 0, 'N/A'
    if spy_90d_chg >= 10:   return +2, f'S&P500 90일 {spy_90d_chg:+.1f}% — 강세'
    if spy_90d_chg >= 2:    return +1, f'S&P500 90일 {spy_90d_chg:+.1f}% — 상승'
    if spy_90d_chg >= -5:   return -1, f'S&P500 90일 {spy_90d_chg:+.1f}% — 약세'
    return                  -2,        f'S&P500 90일 {spy_90d_chg:+.1f}% — 급락'

def _score_dxy(dxy_30d_chg):
    """달러지수 30일 변화 (달러 강세 = 유동성 수축)."""
    if dxy_30d_chg is None: return 0, 'N/A'
    if dxy_30d_chg <= -2:   return +2, f'달러 {dxy_30d_chg:+.1f}% — 달러 약세 (유동성 ↑)'
    if dxy_30d_chg <= 0:    return +1, f'달러 {dxy_30d_chg:+.1f}% — 달러 보합'
    if dxy_30d_chg <= 2:    return -1, f'달러 {dxy_30d_chg:+.1f}% — 달러 강세'
    return                  -2,        f'달러 {dxy_30d_chg:+.1f}% — 달러 급등 (유동성 ↓)'

_REGIME_PROMPT = """\
오늘 날짜: {today}
시장 Regime 점수: {score}점
국면: {regime}

시장 데이터:
{data_lines}

위 데이터를 바탕으로 현재 시장 상황을 2~3줄로 요약하고,
GM Capital 포트폴리오(반도체·미국기술주ETF·신흥국ETF)에 대한 함의를 1줄로 추가하라.

형식:
[시황] 2~3줄 요약
[포트폴리오 함의] 1줄

간결하게. 50자 이내 각 줄."""

def _send_telegram(text):
    token   = _cfg['telegram_token']
    chat_id = _cfg['telegram_chat_id']
    requests.post(
        f'https://api.telegram.org/bot{token}/sendMessage',
        json={'chat_id': chat_id, 'text': text, 'parse_mode': 'HTML'},
        timeout=15,
    )

def run_regime():
    print(f'[{datetime.now().strftime("%Y-%m-%d %H:%M")}] Regime 엔진 실행...')

    # ── 데이터 수집 ─────────────────────────────────────────────────
    vix       = _fetch('^VIX')
    rate_10y  = _fetch('^TNX')                   # 10년물 금리 (%)
    spy_chg   = _fetch_change_pct('SPY',  90)    # S&P500 90일
    dxy_chg   = _fetch_change_pct('DX-Y.NYB', 30) # 달러지수 30일

    print(f'  VIX={vix}  10Y={rate_10y}  SPY90d={spy_chg}  DXY30d={dxy_chg}')

    # ── 점수 계산 ────────────────────────────────────────────────────
    s_vix,  l_vix  = _score_vix(vix)
    s_rate, l_rate = _score_rate(rate_10y)
    s_spy,  l_spy  = _score_spy(spy_chg)
    s_dxy,  l_dxy  = _score_dxy(dxy_chg)

    total = s_vix + s_rate + s_spy + s_dxy
    regime, alloc = _get_regime(total)

    # ── Groq 시황 요약 ───────────────────────────────────────────────
    data_lines = '\n'.join([l_vix, l_rate, l_spy, l_dxy])
    try:
        groq_summary = groq_client.call(
            _REGIME_PROMPT.format(
                today=datetime.now().strftime('%Y-%m-%d'),
                score=total, regime=regime,
                data_lines=data_lines,
            ),
            max_tokens=200, temperature=0.3,
            model='llama-3.3-70b-versatile',
        )
    except Exception as e:
        groq_summary = f'요약 생성 오류: {e}'

    # ── 국면 아이콘 ──────────────────────────────────────────────────
    if total >= 4:    icon = '🚀'
    elif total >= 1:  icon = '📈'
    elif total == 0:  icon = '⚖️'
    elif total >= -3: icon = '📉'
    else:             icon = '🔴'

    def _bar(score):
        return '🟢' if score > 0 else ('🔴' if score < 0 else '⚪')

    now_str = datetime.now().strftime('%m/%d %H:%M')
    lines = [
        f'<b>{icon} 시장 Regime  {now_str}</b>',
        f'<b>국면: {regime}  ({total:+d}점)</b>',
        '',
        '<b>📊 채점 내역</b>',
        f'{_bar(s_vix)}  변동성(VIX):  {s_vix:+d}점  {l_vix}',
        f'{_bar(s_rate)} 금리(10Y):    {s_rate:+d}점  {l_rate}',
        f'{_bar(s_spy)}  모멘텀(SPY):  {s_spy:+d}점  {l_spy}',
        f'{_bar(s_dxy)}  유동성(DXY):  {s_dxy:+d}점  {l_dxy}',
        '',
        f'<b>💼 자산배분 권고</b>',
        f'{alloc}',
        '',
        groq_summary,
        '',
        '<i>* GM Capital Regime 엔진 — 매일 22:30 자동 점수화</i>',
    ]

    _send_telegram('\n'.join(lines))
    print(f'✅ Regime 리포트 발송 완료! 국면: {regime} ({total:+d}점)')


if __name__ == '__main__':
    run_regime()
