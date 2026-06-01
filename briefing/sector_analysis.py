"""
GM Capital 유망 섹터 분석 — 매주 일요일 08:30
11개 섹터 모멘텀 스코어 → 상위 4섹터 + 시총 Top2 + ROY 추천
"""
import yfinance as yf
import requests, json, os, sys, subprocess
from datetime import datetime

sys.stdout.reconfigure(encoding='utf-8')

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
REPO_DIR = os.path.normpath(os.path.join(BASE_DIR, '..'))

with open(os.path.join(BASE_DIR, 'config.json'), 'r', encoding='utf-8') as f:
    config = json.load(f)

TOKEN   = config['telegram_token']
CHAT_ID = config['telegram_chat_id']

# 섹터별 대표 종목 (시총 내림차순)
SECTORS = [
    ('XLK',  '기술',        ['NVDA', 'AAPL', 'MSFT', 'AVGO', 'ORCL']),
    ('XLC',  '커뮤니케이션', ['META', 'GOOGL', 'NFLX', 'CMCSA', 'DIS']),
    ('XLY',  '임의소비재',  ['AMZN', 'TSLA', 'HD',   'MCD',  'NKE']),
    ('XLF',  '금융',        ['JPM',  'V',    'MA',   'BAC',  'WFC']),
    ('XLV',  '헬스케어',    ['LLY',  'UNH',  'JNJ',  'ABBV', 'MRK']),
    ('XLI',  '산업',        ['GE',   'CAT',  'HON',  'UNP',  'RTX']),
    ('XLE',  '에너지',      ['XOM',  'CVX',  'COP',  'EOG',  'SLB']),
    ('XLB',  '소재',        ['LIN',  'APD',  'SHW',  'ECL',  'FCX']),
    ('XLP',  '필수소비재',  ['WMT',  'COST', 'PG',   'KO',   'PEP']),
    ('XLRE', '부동산',      ['PLD',  'AMT',  'CCI',  'EQIX', 'PSA']),
    ('XLU',  '유틸리티',   ['NEE',  'SO',   'DUK',  'SRE',  'AEP']),
]

# 포트폴리오 연계 섹터
PORTFOLIO_LINK = {
    'XLK': 'TSM·MU',
    'XLC': 'GGLL',
}


def get_sector_momentum():
    """섹터 ETF 1주·1달·3달 수익률 → 모멘텀 스코어"""
    etfs = [s[0] for s in SECTORS]
    try:
        data = yf.download(etfs, period='95d', progress=False, auto_adjust=True)['Close']
        results = []
        for etf, name, stocks in SECTORS:
            try:
                px  = data[etf].dropna()
                cur = float(px.iloc[-1])
                w1  = float((cur - px.iloc[-6])  / px.iloc[-6]  * 100) if len(px) >= 6  else 0
                m1  = float((cur - px.iloc[-22]) / px.iloc[-22] * 100) if len(px) >= 22 else 0
                m3  = float((cur - px.iloc[-66]) / px.iloc[-66] * 100) if len(px) >= 66 else 0
                score = w1 * 3 + m1 * 2 + m3  # 단기 가중
                results.append({'etf': etf, 'name': name, 'stocks': stocks,
                                 'w1': w1, 'm1': m1, 'm3': m3, 'score': score})
            except:
                pass
        results.sort(key=lambda x: x['score'], reverse=True)
        return results
    except Exception as e:
        print(f'  [섹터 조회 실패] {e}')
        return []


def get_stock_perf(symbols):
    """종목 현재가·일간 등락률 배치 조회"""
    try:
        data = yf.download(symbols, period='2d', progress=False, auto_adjust=True)['Close']
        out = {}
        for sym in symbols:
            try:
                col  = data[sym] if sym in data.columns else data
                cur  = float(col.iloc[-1])
                prev = float(col.iloc[-2])
                out[sym] = {'price': cur, 'chg': (cur - prev) / prev * 100}
            except:
                out[sym] = {'price': 0, 'chg': 0}
        return out
    except:
        return {}


def make_rec(s):
    """포트폴리오·매크로 기반 ROY 추천 한 줄"""
    etf, w1, m1 = s['etf'], s['w1'], s['m1']
    up = w1 > 0 and m1 > 0
    strong = w1 > 2

    if etf == 'XLK':
        if strong:   return "TSM·MU 보유 직접 수혜 — 기술 강세 지속 시 현 포지션 유지 권장"
        if w1 < -1.5: return "기술 섹터 단기 조정 — TSM·MU 손절 기준 재확인 권장"
        return "TSM·MU 연계 — 섹터 방향 확인 후 추가 매수 판단"

    if etf == 'XLC':
        if strong:    return "GGLL(Alphabet 2x) 직접 수혜 — 레버리지 수익 배가 기대. 비중 유지"
        if w1 < -1.5: return "GGLL 레버리지 낙폭 주의 — 즉시 비중 축소 검토"
        return "GGLL 연계 — 현 포지션 유지"

    if etf in ['XLV', 'XLP', 'XLU']:
        if strong: return "방어 섹터 강세 = 위험회피 신호 — 성장주(TSM·MU·GGLL) 비중 점검 권장"
        return "방어 섹터 — 경기 둔화 헤지 관심 시 모니터링 가능"

    if etf == 'XLE':
        if strong: return "에너지 강세 → 인플레 재점화 리스크 — 연준 긴축 장기화 시 성장주 압박"
        return "에너지 — 유가 동향 주시. 현 포트폴리오 직접 영향 없음"

    if etf == 'XLY':
        if up: return "소비재 강세 = 경기 확장 신호 — 성장주에 간접 긍정적"
        return "소비재 — 경기 지표로 활용"

    if etf == 'XLF':
        if strong: return "금융 강세 = 금리 고원 지속 가능성 — 성장주 밸류에이션 중립"
        return "금융 — 금리 방향성 참고 지표로 활용"

    if up: return "현재 미보유 섹터 — 모멘텀 지속 시 모니터링 추가 검토 가능"
    return "현재 미보유 섹터 — 모니터링 유지"


def push_sector_data(payload):
    """sector_data.json 저장 + GitHub push → 홈페이지 자동 반영"""
    path = os.path.join(REPO_DIR, 'sector_data.json')
    with open(path, 'w', encoding='utf-8') as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
    try:
        subprocess.run(['git', '-C', REPO_DIR, 'add', 'sector_data.json'],
                       check=True, capture_output=True)
        subprocess.run(['git', '-C', REPO_DIR, 'commit', '-m',
                        f'섹터 분석 {payload["updated"]}'],
                       check=True, capture_output=True)
        subprocess.run(['git', '-C', REPO_DIR, 'push'],
                       check=True, capture_output=True)
        print('  [DES] 섹터 데이터 홈페이지 업로드 완료')
    except Exception as e:
        print(f'  [git push 실패] {e}')


def send(text):
    requests.post(
        f'https://api.telegram.org/bot{TOKEN}/sendMessage',
        json={'chat_id': CHAT_ID, 'text': text, 'parse_mode': 'HTML',
              'disable_web_page_preview': True},
        timeout=15
    )


def main():
    now    = datetime.now()
    day_kr = ['월','화','수','목','금','토','일'][now.weekday()]

    print('  [DAT] 11개 섹터 모멘텀 분석 중...')
    sectors = get_sector_momentum()
    if not sectors:
        print('  섹터 데이터 조회 실패')
        return

    top4 = sectors[:4]
    bot3 = sectors[-3:]

    top_syms  = list({sym for s in top4 for sym in s['stocks'][:2]})
    print(f'  [DAT] 상위 종목 {len(top_syms)}개 조회 중...')
    stock_data = get_stock_perf(top_syms)

    # 홈페이지용 JSON
    json_sectors = []
    for s in top4:
        stocks_info = []
        for sym in s['stocks'][:2]:
            sd = stock_data.get(sym, {})
            stocks_info.append({'sym': sym,
                                 'price': round(sd.get('price', 0), 2),
                                 'chg':   round(sd.get('chg', 0), 2)})
        json_sectors.append({
            'etf': s['etf'], 'name': s['name'],
            'w1': round(s['w1'], 2), 'm1': round(s['m1'], 2), 'm3': round(s['m3'], 2),
            'stocks': stocks_info,
            'link': PORTFOLIO_LINK.get(s['etf'], ''),
            'rec': make_rec(s),
        })
    push_sector_data({'updated': now.strftime('%Y-%m-%d %H:%M'), 'sectors': json_sectors})

    # ── 텔레그램 ──
    msg  = f"📈 <b>ROY 유망 섹터 분석</b>\n"
    msg += f"{now.strftime('%Y년 %m월 %d일')} ({day_kr}요일)\n\n"

    medals = ['🥇', '🥈', '🥉', '4️⃣']
    for idx, s in enumerate(top4):
        sw1 = ('+' if s['w1'] >= 0 else '') + f"{s['w1']:.1f}%"
        sm1 = ('+' if s['m1'] >= 0 else '') + f"{s['m1']:.1f}%"
        sm3 = ('+' if s['m3'] >= 0 else '') + f"{s['m3']:.1f}%"
        link = PORTFOLIO_LINK.get(s['etf'])
        badge = f" 🔗 <b>{link} 연계</b>" if link else ''

        msg += f"{medals[idx]} <b>{s['name']} ({s['etf']})</b>{badge}\n"
        msg += f"   1주 {sw1} | 1달 {sm1} | 3달 {sm3}\n"

        for sym in s['stocks'][:2]:
            sd    = stock_data.get(sym, {})
            price = sd.get('price', 0)
            chg   = sd.get('chg', 0)
            arrow = '▲' if chg >= 0 else '▼'
            sc    = ('+' if chg >= 0 else '') + f"{chg:.2f}%"
            msg  += f"   └ <b>{sym}</b>  ${price:.1f}  {arrow} {sc}\n"

        msg += f"   <i>{make_rec(s)}</i>\n\n"

    msg += "─────────────────\n"
    msg += "📉 <b>부진 섹터</b>\n"
    for s in bot3:
        sw = ('+' if s['w1'] >= 0 else '') + f"{s['w1']:.1f}%"
        msg += f"  {s['name']} ({s['etf']}): 1주 {sw}\n"

    msg += "\n─────────────────\n"
    msg += "🏢 <i>GM Capital · DAT → ROY</i>"

    send(msg)
    print(f"[{now:%Y-%m-%d %H:%M}] 유망 섹터 분석 전송 완료")


if __name__ == '__main__':
    main()
