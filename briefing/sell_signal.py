"""
GM Capital 사전 분할매도 신호 — AP팀
보유 6종목 기술적 분석 → RSI 과매수 / 50일 MA 이탈 / VIX 급등 시 경보
매일 22:30 실행 (미국 장 개시 전 최종 점검)
"""
import json, os, sys, requests
import yfinance as yf
import yaml
from datetime import datetime, date

sys.stdout.reconfigure(encoding='utf-8')

import groq_client

BASE_DIR   = os.path.dirname(os.path.abspath(__file__))
LOG_FILE   = os.path.join(BASE_DIR, 'sell_signal_log.json')
CACHE_FILE = os.path.join(BASE_DIR, 'sell_signal_cache.json')

with open(os.path.join(BASE_DIR, 'config.json'), 'r', encoding='utf-8') as f:
    _cfg = json.load(f)
with open(os.path.join(BASE_DIR, 'config.yaml'), 'r', encoding='utf-8') as f:
    _ycfg = yaml.safe_load(f)

HOLDINGS = _ycfg['universe']['holdings_watch']   # [TSM, MU, GGLL, IEMG, SPY, QQQ]

# 레버리지 ETF는 RSI 기준 낮춤 (2x 이므로 더 빨리 과매수 도달)
RSI_THRESHOLD = {sym: (65 if sym == 'GGLL' else 70) for sym in HOLDINGS}


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

def _load_cache():
    try:
        with open(CACHE_FILE, 'r', encoding='utf-8') as f:
            return json.load(f)
    except Exception:
        return {}

def _save_cache(data):
    with open(CACHE_FILE, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False)


# ── VIX 조회 ──────────────────────────────────────────────────────────
def _get_vix():
    cache = _load_cache()
    try:
        hist    = yf.Ticker('^VIX').history(period='10d')
        cur     = round(float(hist['Close'].iloc[-1]), 2)
        wk_ago  = float(hist['Close'].iloc[-6]) if len(hist) >= 6 else cur
        chg_pct = round((cur - wk_ago) / wk_ago * 100, 1)
        cache['vix'] = cur
        cache['vix_chg'] = chg_pct
        _save_cache(cache)
        return cur, chg_pct
    except Exception as e:
        print(f'  [VIX 조회 실패] {e} → 캐시 사용')
        return cache.get('vix', 18.0), cache.get('vix_chg', 0.0)


# ── 보유종목 기술적 분석 ──────────────────────────────────────────────
def _analyze():
    try:
        data  = yf.download(HOLDINGS, period='65d', interval='1d',
                            auto_adjust=True, progress=False, threads=True)
        close = data['Close']
    except Exception as e:
        print(f'  [데이터 오류] {e}')
        return []

    results = []
    for sym in HOLDINGS:
        try:
            col = close[sym].dropna() if sym in close.columns else None
            if col is None or len(col) < 20:
                continue

            cur     = float(col.iloc[-1])
            ma50    = float(col.tail(50).mean()) if len(col) >= 50 else float(col.mean())
            high20  = float(col.tail(20).max())
            rsi_val = float(_rsi(col).iloc[-1])
            ret5    = round((cur - float(col.iloc[-6])) / float(col.iloc[-6]) * 100, 1) if len(col) >= 6 else 0.0
            from_high = round((cur - high20) / high20 * 100, 1)

            reasons  = []
            severity = 0
            threshold = RSI_THRESHOLD[sym]

            if rsi_val >= threshold:
                reasons.append(f'RSI {rsi_val:.0f} 과매수')
                severity += 2
            elif rsi_val >= threshold - 5:
                reasons.append(f'RSI {rsi_val:.0f} 과매수 경계')
                severity += 1

            if cur < ma50 * 0.985:
                reasons.append(f'50일MA({ma50:.1f}) 2% 이탈')
                severity += 2
            elif cur < ma50:
                reasons.append(f'50일MA({ma50:.1f}) 근접 이탈')
                severity += 1

            if from_high <= -10:
                reasons.append(f'20일 고점 대비 {from_high:.1f}%')
                severity += 1

            if severity >= 2:
                results.append({
                    'sym':      sym,
                    'price':    round(cur, 2),
                    'rsi':      round(rsi_val, 1),
                    'ma50':     round(ma50, 2),
                    'from_high': from_high,
                    'ret5':     ret5,
                    'reasons':  reasons,
                    'severity': severity,
                })
        except Exception as e:
            print(f'  [{sym}] 분석 오류: {e}')

    return sorted(results, key=lambda x: x['severity'], reverse=True)


# ── Groq 분할매도 전략 ────────────────────────────────────────────────
_SELL_PROMPT = """\
GM Capital 보유종목: {sym} (현재가 ${price})
RSI {rsi} | 50일MA ${ma50} | 20일 고점 대비 {from_high:.1f}% | 최근5일 {ret5:+.1f}%
VIX {vix:.1f} ({vix_chg:+.1f}% 주간변화)
경고 사유: {reasons}

분할매도 전략을 제시하라. 형식 엄수:
1차 매도: X% — 구체적 조건
2차 매도: X% — 조건 (없으면 생략)
유지판단: 과매수 vs 추세전환 구분 (30자 이내)

원칙: GGLL(2x레버리지)는 일반주보다 적극적 매도 기준 적용"""


def _groq_strategy(item, vix, vix_chg):
    try:
        return groq_client.call(
            _SELL_PROMPT.format(
                sym=item['sym'], price=item['price'], rsi=item['rsi'],
                ma50=item['ma50'], from_high=item['from_high'],
                ret5=item['ret5'], vix=vix, vix_chg=vix_chg,
                reasons=' / '.join(item['reasons']),
            ),
            max_tokens=200, temperature=0.2,
        )
    except Exception:
        return '1차 매도: 20% — 현 시점 일부 실현\n2차 매도: 20% — 추가 하락 시\n유지판단: 기술적 과열 조정 가능성'


# ── 텔레그램 ──────────────────────────────────────────────────────────
def _send(text):
    requests.post(
        f"https://api.telegram.org/bot{_cfg['telegram_token']}/sendMessage",
        json={'chat_id': _cfg['telegram_chat_id'], 'text': text, 'parse_mode': 'HTML'},
        timeout=15,
    )


# ── 메인 ─────────────────────────────────────────────────────────────
def run_sell_signal():
    print(f'[{datetime.now().strftime("%Y-%m-%d %H:%M")}] 사전 분할매도 신호 점검...')
    log       = _load_log()
    vix, vchg = _get_vix()
    vix_alert = vix >= 25 or (vchg is not None and vchg >= 30)

    alerts = _analyze()
    fresh  = [a for a in alerts if a['sym'] not in log['alerted']]

    if not fresh and not vix_alert:
        print(f'  VIX {vix:.1f} | 분할매도 조건 없음 (0건 정상)')
        return

    now_str = datetime.now().strftime('%m/%d %H:%M')
    lines   = [f'<b>🔴 분할매도 사전 신호  {now_str}</b>',
               f'VIX {vix:.1f} ({vchg:+.1f}% 주간)', '']

    if vix_alert and not fresh:
        lines += [
            '<b>⚠️ VIX 25 초과 또는 주간 30%+ 급등</b>',
            '→ 전 보유종목 분할매도 1차 검토 시점',
            '→ 이번 사이클 최고점 근처일 가능성',
        ]

    for item in fresh[:3]:
        strategy = _groq_strategy(item, vix, vchg)
        icon     = '🔴' if item['severity'] >= 3 else '🟡'
        lines   += [
            f'{icon} <b>{item["sym"]}  ${item["price"]}  RSI {item["rsi"]}</b>',
            f'사유: {" / ".join(item["reasons"])}',
            strategy.strip(),
            '',
        ]
        log['alerted'].append(item['sym'])

    _save_log(log)
    lines.append('<i>* AP팀 사전 분할매도 신호 — CEO 최종 판단 우선</i>')
    _send('\n'.join(lines))
    print(f'✅ 분할매도 신호 {len(fresh)}건 발송! (VIX={vix:.1f})')


if __name__ == '__main__':
    run_sell_signal()
