"""
매크로 지표 발표 즉시 브리핑
주요 경제 지표 발표 당일 → 포트폴리오 영향 분석 → 텔레그램 전송

실행 스케줄 (KST 기준):
  22:30 KST = 09:30 ET → CPI·NFP·GDP 발표(08:30 ET) 후 1시간
  04:00 KST = 15:00 ET → FOMC 발표(14:00 ET) 후 1시간
  [08:30 KST 실행 제거 — 미국은 전날 밤으로 지표 발표 전]
"""
import json, os, sys, requests, xml.etree.ElementTree as ET
from datetime import datetime, date

import groq_client

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
with open(os.path.join(BASE_DIR, 'config.json'), 'r', encoding='utf-8') as f:
    _cfg = json.load(f)


# ── 2026 주요 경제 지표 캘린더 ───────────────────────────────────────
# 형식: (날짜, 지표명, 중요도 H/M, 설명)
MACRO_CALENDAR = [
    # FOMC
    ('2026-01-29', 'FOMC 금리 결정',         'H', '연준 기준금리 결정 — 인하/동결/인상 발표'),
    ('2026-03-19', 'FOMC 금리 결정',         'H', '연준 기준금리 결정'),
    ('2026-05-07', 'FOMC 금리 결정',         'H', '연준 기준금리 결정'),
    ('2026-06-18', 'FOMC 금리 결정',         'H', '연준 기준금리 결정 + 점도표'),
    ('2026-07-29', 'FOMC 금리 결정',         'H', '연준 기준금리 결정'),
    ('2026-09-17', 'FOMC 금리 결정',         'H', '연준 기준금리 결정 + 점도표'),
    ('2026-11-05', 'FOMC 금리 결정',         'H', '연준 기준금리 결정'),
    ('2026-12-17', 'FOMC 금리 결정',         'H', '연준 기준금리 결정 + 점도표'),
    # CPI (매월 중순 수요일)
    ('2026-01-14', 'CPI 소비자물가',          'H', '인플레이션 핵심 지표'),
    ('2026-02-11', 'CPI 소비자물가',          'H', '인플레이션 핵심 지표'),
    ('2026-03-11', 'CPI 소비자물가',          'H', '인플레이션 핵심 지표'),
    ('2026-04-15', 'CPI 소비자물가',          'H', '인플레이션 핵심 지표'),
    ('2026-05-13', 'CPI 소비자물가',          'H', '인플레이션 핵심 지표'),
    ('2026-06-10', 'CPI 소비자물가',          'H', '인플레이션 핵심 지표'),
    ('2026-07-15', 'CPI 소비자물가',          'H', '인플레이션 핵심 지표'),
    ('2026-08-12', 'CPI 소비자물가',          'H', '인플레이션 핵심 지표'),
    ('2026-09-09', 'CPI 소비자물가',          'H', '인플레이션 핵심 지표'),
    ('2026-10-14', 'CPI 소비자물가',          'H', '인플레이션 핵심 지표'),
    ('2026-11-12', 'CPI 소비자물가',          'H', '인플레이션 핵심 지표'),
    ('2026-12-10', 'CPI 소비자물가',          'H', '인플레이션 핵심 지표'),
    # NFP 비농업 고용 (매월 첫째 금요일)
    ('2026-01-09', '비농업 고용지수 NFP',     'H', '고용 시장 강도 — 금리 방향 영향'),
    ('2026-02-06', '비농업 고용지수 NFP',     'H', '고용 시장 강도'),
    ('2026-03-06', '비농업 고용지수 NFP',     'H', '고용 시장 강도'),
    ('2026-04-03', '비농업 고용지수 NFP',     'H', '고용 시장 강도'),
    ('2026-05-01', '비농업 고용지수 NFP',     'H', '고용 시장 강도'),
    ('2026-06-05', '비농업 고용지수 NFP',     'H', '고용 시장 강도'),
    ('2026-07-10', '비농업 고용지수 NFP',     'H', '고용 시장 강도'),
    ('2026-08-07', '비농업 고용지수 NFP',     'H', '고용 시장 강도'),
    ('2026-09-04', '비농업 고용지수 NFP',     'H', '고용 시장 강도'),
    ('2026-10-02', '비농업 고용지수 NFP',     'H', '고용 시장 강도'),
    ('2026-11-06', '비농업 고용지수 NFP',     'H', '고용 시장 강도'),
    ('2026-12-04', '비농업 고용지수 NFP',     'H', '고용 시장 강도'),
    # GDP (분기말 익월)
    ('2026-01-30', 'GDP 성장률 (4분기)',       'H', '미국 경제성장률 속보치'),
    ('2026-04-29', 'GDP 성장률 (1분기)',       'H', '미국 경제성장률 속보치'),
    ('2026-07-30', 'GDP 성장률 (2분기)',       'H', '미국 경제성장률 속보치'),
    ('2026-10-29', 'GDP 성장률 (3분기)',       'H', '미국 경제성장률 속보치'),
    # PPI (CPI 하루 전)
    ('2026-01-15', 'PPI 생산자물가',          'M', '기업 비용 압력 — CPI 선행지표'),
    ('2026-06-11', 'PPI 생산자물가',          'M', '기업 비용 압력'),
    # 소비자신뢰지수
    ('2026-06-30', '미시간 소비자신뢰지수',   'M', '소비 심리 — 리테일 영향'),
]


def _get_today_events():
    today_str = date.today().strftime('%Y-%m-%d')
    return [(name, level, desc)
            for (d, name, level, desc) in MACRO_CALENDAR
            if d == today_str]


# 한국어 이벤트명 → 영문 검색 쿼리 매핑 (Google News 영문 기사 수집용)
_EN_QUERY = {
    'FOMC 금리 결정':      'FOMC Fed interest rate decision',
    'CPI 소비자물가':       'CPI consumer price index inflation',
    '비농업 고용지수 NFP':  'NFP nonfarm payrolls jobs report',
    'GDP 성장률':           'US GDP growth rate',
    'PPI 생산자물가':       'PPI producer price index',
    '미시간 소비자신뢰지수': 'Michigan consumer sentiment index',
}

# ── 뉴스로 실제 수치 파악 ─────────────────────────────────────────────
def _fetch_macro_news(event_name):
    """Google News RSS에서 오늘 지표 관련 실제 발표 수치 수집"""
    # 영문 쿼리로 변환 (한국어 검색은 영문 기사가 안 잡힘)
    en_query = next((v for k, v in _EN_QUERY.items() if k in event_name), event_name)
    query = en_query.replace(' ', '+') + '+today'
    url   = (f'https://news.google.com/rss/search?q={query}'
             f'&hl=en-US&gl=US&ceid=US:en')
    try:
        resp = requests.get(url, timeout=10,
                            headers={'User-Agent': 'Mozilla/5.0'})
        root = ET.fromstring(resp.content)
        headlines = []
        for item in root.findall('.//item')[:8]:
            t = item.find('title')
            if t is not None and t.text:
                headlines.append(t.text.strip())
        return headlines
    except Exception:
        return []


# ── LLM 포트폴리오 영향 분석 ─────────────────────────────────────────
_MACRO_PROMPT = """\
오늘 미국 경제지표 "{event}"가 발표됐다.
관련 뉴스 헤드라인:
{headlines}

아래 GM Capital 포트폴리오에 미치는 영향을 한국어로 분석해줘.
포트폴리오: TSMC(TSM), Micron(MU), GGLL(Alphabet 2x), IEMG, S&P500(SPY), QQQ

출력 형식:
1. 지표 요약: 실제 수치와 시장 예상 대비 한줄 (30자 이내)
2. 시장 방향: 전체 증시 단기 영향 (30자 이내)
3. 포트폴리오 영향:
   - 긍정 영향 종목과 이유 (각 30자 이내)
   - 부정 영향 종목과 이유 (각 30자 이내)
4. 핵심 주목: 투자자가 지금 당장 봐야 할 것 한줄

한자 금지. 단정 말고 "~가능성" "~예상" 어조로."""


def _analyze_impact(event_name, headlines):
    if not headlines:
        return None
    hl_text = '\n'.join(f'- {h}' for h in headlines[:6])
    try:
        return groq_client.call(_MACRO_PROMPT.format(event=event_name, headlines=hl_text), max_tokens=500)
    except Exception as e:
        print(f'  [LLM 오류] {e}')
        return None




def send_telegram(text):
    token   = _cfg['telegram_token']
    chat_id = _cfg['telegram_chat_id']
    url     = f'https://api.telegram.org/bot{token}/sendMessage'
    requests.post(url,
        json={'chat_id': chat_id, 'text': text, 'parse_mode': 'HTML'},
        timeout=15)


# ── 메시지 포맷 ──────────────────────────────────────────────────────
def format_macro_alert(event_name, level, desc, analysis):
    icon      = '🏛' if 'FOMC' in event_name else ('📊' if level == 'H' else '📋')
    now_str   = datetime.now().strftime('%m/%d %H:%M')
    lines = [
        f'<b>{icon} 매크로 지표 발표  {now_str}</b>',
        f'<b>{event_name}</b>  <i>{desc}</i>',
        '',
    ]
    if analysis:
        for line in analysis.splitlines():
            line = line.strip()
            if line:
                lines.append(line)
    else:
        lines.append('뉴스 수집 실패 — 수동 확인 필요')

    lines.append('')
    lines.append('<i>* AP팀 — 발표 수치 확인 후 포지션 검토</i>')
    return '\n'.join(lines)


# ── 메인 ────────────────────────────────────────────────────────────
def run_macro_check(force_event=None):
    if force_event:
        events = [(force_event, 'H', '수동 테스트')]
    else:
        events = _get_today_events()

    if not events:
        print(f'[{datetime.now().strftime("%Y-%m-%d %H:%M")}] 오늘 주요 지표 발표 없음')
        return

    print(f'[{datetime.now().strftime("%Y-%m-%d %H:%M")}] 매크로 지표 발표일 감지: {len(events)}건')

    for event_name, level, desc in events:
        print(f'  처리중: {event_name}')
        headlines = _fetch_macro_news(event_name)
        analysis  = _analyze_impact(event_name, headlines)
        msg       = format_macro_alert(event_name, level, desc, analysis)
        send_telegram(msg)
        print(f'  전송 완료')


if __name__ == '__main__':
    force = sys.argv[1] if len(sys.argv) > 1 else None
    run_macro_check(force_event=force)
