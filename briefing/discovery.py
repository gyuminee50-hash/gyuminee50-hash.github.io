"""
발굴 엔진 — 신호 먼저(signal-first) 구조  v1.0
==============================================
흐름: 광범위 뉴스 RSS → 8B 1차 필터(촉매+종목 추출)
      → 보유종목 제외 + 14일 재탕 제외
      → 70B 루브릭 채점 (5항목)
      → 후행 즉사(미래성 0점) + 절대바(7점) 통과
      → signals DB 로깅 + 텔레그램 발송
"""
import json, os, re, sqlite3, sys, yaml, requests, xml.etree.ElementTree as ET
from datetime import datetime, timedelta, date, timezone

sys.stdout.reconfigure(encoding='utf-8')

import yfinance as yf
import groq_client
from db_setup import DB_PATH, init_db

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

with open(os.path.join(BASE_DIR, 'config.json'), 'r', encoding='utf-8') as _f:
    _api_cfg = json.load(_f)
with open(os.path.join(BASE_DIR, 'config.yaml'), 'r', encoding='utf-8') as _f:
    _cfg = yaml.safe_load(_f)

EXCLUDE_HOLDINGS = set(_cfg['universe']['exclude_holdings'])
NOVELTY_DAYS     = _cfg['discovery']['novelty_days']
RUBRIC_THRESHOLD = _cfg['rubric']['threshold']
SCOUT_MODEL      = _cfg['models']['scout']
JUDGE_MODEL      = _cfg['models']['judge']

# 고정 signal_type 목록 — 스카우트 결과를 이 목록으로 강제 정규화
VALID_SIGNAL_TYPES = {
    '설비증설', '대규모수주', '신규계약', '공급망변화',
    '정책수혜', '실적서프라이즈', 'M&A', '기타',
}

def _normalize_signal_type(raw: str) -> str:
    """공백·대소문자 정규화 후 고정 목록에 없으면 '기타'로 치환."""
    if not raw:
        return '기타'
    s = raw.strip().replace(' ', '')   # "정책 수혜" → "정책수혜"
    return s if s in VALID_SIGNAL_TYPES else '기타'

# 루브릭 JSON 키 오타 교정 매핑
_RUBRIC_KEY_FIX = {'출처신료도': '출처신뢰도', '출처신뢰도_근거_': '출처신뢰도_근거'}

# 발굴 전용 광범위 뉴스 RSS (보유종목 뉴스 아닌 시장 전체 스캔)
DISCOVERY_FEEDS = [
    ('Reuters',       'https://feeds.reuters.com/reuters/businessNews'),
    ('AP',            'https://feeds.apnews.com/apf-business'),
    ('WSJ',           'https://feeds.a.dj.com/rss/WSJcomUSBusiness.xml'),
    ('CNBC Markets',  'https://search.cnbc.com/rs/search/combinedcms/view.xml?partnerId=wrss01&id=100003114'),
    ('MarketWatch',   'https://feeds.content.dowjones.io/public/rss/mw_topstories'),
    ('Investing.com', 'https://www.investing.com/rss/news.rss'),
    ('SeekingAlpha',  'https://seekingalpha.com/feed.xml'),
]

_SCOUT_PROMPT = """\
다음 뉴스 헤드라인에서 특정 주식에 대한 "투자 촉매"가 있는지 판단하라.

헤드라인: {headline}

JSON 형식으로만 응답. 마크다운 없이 순수 JSON:
{{"has_catalyst": true/false, "ticker": "티커 또는 null", "market": "US", "signal_type": "설비증설/대규모수주/신규계약/공급망변화/정책수혜/실적서프라이즈/M&A/기타", "reason": "한 줄 이유"}}

signal_type 선택 규칙 (반드시 이 중 하나):
- 설비증설: 공장·생산설비 신규 투자
- 대규모수주: 계약 규모 숫자 있는 수주
- 신규계약: 파트너십·공급계약 체결
- 공급망변화: 공급사 교체·단가 변화
- 정책수혜: 정부 보조금·규제 완화
- 실적서프라이즈: 예상치 초과 실적 (미래 실적 가이던스만 해당, 이미 발표된 결과 ✕)
- M&A: 인수합병 (루머 단계만, 완료 발표 ✕)
- 기타: 위 어디도 해당 없음

촉매 없음 → has_catalyst: false:
- 일반 시황/지수 동향, CEO 의견 개진, 이미 발표된 과거 실적
- 영어 헤드라인이 분석 기사/칼럼 제목인 경우
- 특정 상장 주식 티커와 연결 불가"""

_RUBRIC_PROMPT = """\
다음 투자 뉴스에 대한 선행신호 품질을 루브릭으로 채점하라.

헤드라인: {headline}
종목: {ticker}
신호유형: {signal_type}

5개 항목 각 0~2점 채점. JSON으로만 (마크다운 없이):
{{"미래성": 0, "미래성_근거": "한 줄", "인과명확성": 0, "인과명확성_근거": "한 줄", "반영여부": 0, "반영여부_근거": "한 줄", "구체성": 0, "구체성_근거": "한 줄", "출처신뢰도": 0, "출처신뢰도_근거": "한 줄", "총점": 0, "논리사슬": "한 문단"}}

채점 기준:
- 미래성: 0=이미 발표된 실적/과거가격/완료된 M&A, 1=미래 이벤트 언급, 2=명확한 미래 이벤트 + 시점
- 인과명확성: 0=연결 불명, 1=간접 연결, 2=이벤트→주가 2단계 이내 직접 연결
- 반영여부: 0=점검 안 함, 1=부분 반영 가능성, 2="아직 주가 미반영" 근거 있음
- 구체성: 0=막연, 1=부분 숫자, 2=규모·시점·금액 숫자 포함
- 출처신뢰도: 0=불명/블로그/찌라시, 1=일반 언론, 2=Reuters/AP/WSJ/Bloomberg/FT

핵심 규칙:
- 미래성이 0점이면 즉시 탈락 (총점 무관)
- 총점은 반드시 5개 항목 합산으로 계산 (0~10점)
- 반영여부 2점은 근거가 명확할 때만 부여 — 추정 금지"""

_HOLDINGS_MSG = """\
<b>⚠️ [보유종목 알림] {ticker}</b>
{headline}

{rubric_line}
<i>발굴 채널 기준으로 루브릭 채점한 결과입니다 (보유 모니터링 참고용)</i>"""


def _collect_rss(hours=8):
    """RSS 피드에서 최근 N시간 기사 수집"""
    cutoff = datetime.now(timezone.utc) - timedelta(hours=hours)
    articles = []
    for src, url in DISCOVERY_FEEDS:
        try:
            resp = requests.get(url, timeout=8,
                                headers={'User-Agent': 'Mozilla/5.0'})
            root = ET.fromstring(resp.content)
            for item in root.findall('.//item'):
                title_el = item.find('title')
                if title_el is None:
                    continue
                title = title_el.text.strip() if title_el.text else ''
                if not title:
                    continue
                # pubDate 파싱 (없으면 통과)
                pub_el = item.find('pubDate')
                if pub_el is not None and pub_el.text:
                    try:
                        from email.utils import parsedate_to_datetime
                        pub_dt = parsedate_to_datetime(pub_el.text)
                        if pub_dt.tzinfo is None:
                            pub_dt = pub_dt.replace(tzinfo=timezone.utc)
                        if pub_dt < cutoff:
                            continue
                    except Exception:
                        pass
                link_el = item.find('link')
                articles.append({
                    'title': title,
                    'source': src,
                    'link': link_el.text.strip() if (link_el is not None and link_el.text) else '',
                })
        except Exception as e:
            print(f'  [{src}] RSS 오류: {e}')
    print(f'  뉴스 수집: {len(articles)}건')
    return articles


def _scout_filter(headline, source):
    """8B 1차 필터: 촉매 있는가 + 종목 추출. dict 반환."""
    try:
        raw = groq_client.call(
            _SCOUT_PROMPT.format(headline=headline),
            max_tokens=150, temperature=0.1,
            model=SCOUT_MODEL,
        )
        raw = raw.strip()
        raw = re.sub(r'^```[a-z]*\n?', '', raw)
        raw = re.sub(r'\n?```$', '', raw)
        result = json.loads(raw)
        # signal_type 고정 목록으로 정규화 (공백 제거 + 알 수 없는 값 → '기타')
        result['signal_type'] = _normalize_signal_type(result.get('signal_type', '기타'))
        return result
    except Exception as e:
        return {'has_catalyst': False, 'ticker': None, 'reason': str(e)}


def _is_recent(ticker, days):
    """최근 N일 내 signals 테이블에 동일 종목 있으면 True (재탕 금지)."""
    cutoff = (datetime.now() - timedelta(days=days)).isoformat()
    try:
        conn = sqlite3.connect(DB_PATH)
        row = conn.execute(
            "SELECT 1 FROM signals WHERE ticker=? AND flagged_at>=? LIMIT 1",
            (ticker, cutoff)
        ).fetchone()
        conn.close()
        return row is not None
    except Exception:
        return False


def _judge_rubric(headline, ticker, signal_type):
    """70B 루브릭 채점: dict 반환."""
    try:
        raw = groq_client.call(
            _RUBRIC_PROMPT.format(headline=headline, ticker=ticker, signal_type=signal_type),
            max_tokens=400, temperature=0.2,
            model=JUDGE_MODEL,
        )
        raw = raw.strip()
        raw = re.sub(r'^```[a-z]*\n?', '', raw)
        raw = re.sub(r'\n?```$', '', raw)
        result = json.loads(raw)
        # LLM 오타 키 교정 (예: "출처신료도" → "출처신뢰도")
        for wrong, correct in _RUBRIC_KEY_FIX.items():
            if wrong in result:
                result[correct] = result.pop(wrong)
        return result
    except Exception as e:
        print(f'    [루브릭 오류 {ticker}] {e}')
        return None


_VALID_TICKER = re.compile(r'^[A-Z]{1,5}(\.[A-Z])?$')

def _is_valid_ticker(ticker):
    """US 티커 형식 체크: 1~5 영문자(+옵션 .A/.B 류). 회사명/긴 문자열 차단."""
    if not ticker:
        return False
    return bool(_VALID_TICKER.match(ticker.upper().strip()))


def _get_price(ticker):
    """현재가 조회. 실패 시 None."""
    try:
        info = yf.Ticker(ticker).fast_info
        return float(info.last_price)
    except Exception:
        return None


def _get_bench_price(benchmark='SPY'):
    return _get_price(benchmark)


def _log_signal_db(ticker, market, signal_type, headline, source,
                   reasoning, rubric_score, rubric_detail,
                   price_at_flag, bench_at_flag, benchmark='SPY'):
    """signals 테이블에 신호 기록. 삽입된 row id 반환."""
    try:
        conn = sqlite3.connect(DB_PATH)
        cur = conn.execute(
            """INSERT INTO signals
               (flagged_at,ticker,market,signal_type,headline,source,
                reasoning,rubric_score,rubric_detail,groq_model,
                price_at_flag,benchmark,bench_at_flag)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (datetime.now().isoformat(), ticker, market, signal_type,
             headline, source, reasoning, rubric_score,
             json.dumps(rubric_detail, ensure_ascii=False),
             JUDGE_MODEL, price_at_flag, benchmark, bench_at_flag)
        )
        conn.commit()
        row_id = cur.lastrowid
        conn.close()
        return row_id
    except Exception as e:
        print(f'  [DB 로깅 오류] {e}')
        return None


def _send_telegram(text):
    token   = _api_cfg['telegram_token']
    chat_id = _api_cfg['telegram_chat_id']
    requests.post(
        f'https://api.telegram.org/bot{token}/sendMessage',
        json={'chat_id': chat_id, 'text': text, 'parse_mode': 'HTML'},
        timeout=15,
    )


def run_discovery():
    """발굴 엔진 메인. morning_briefing.py에서 호출."""
    print(f'[{datetime.now().strftime("%Y-%m-%d %H:%M")}] 발굴 스캔 시작 (신호 먼저)...')

    # DB 초기화 보장
    if not os.path.exists(DB_PATH):
        init_db()

    # ── 1. RSS 뉴스 수집 ────────────────────────────────────────────
    articles = _collect_rss(hours=8)
    if not articles:
        print('  뉴스 없음 — 종료')
        return

    # 중복 헤드라인 제거
    seen = set()
    unique = []
    for a in articles:
        key = a['title'][:60]
        if key not in seen:
            seen.add(key)
            unique.append(a)
    articles = unique

    # ── 2. 8B 1차 필터 (촉매 + 종목 추출) ──────────────────────────
    print(f'  8B 1차 필터 중... ({len(articles)}건)')
    candidates = []
    for art in articles:
        result = _scout_filter(art['title'], art['source'])
        if result.get('has_catalyst') and result.get('ticker'):
            t = result['ticker'].upper().strip()
            if _is_valid_ticker(t):
                result['ticker'] = t
                candidates.append({**art, **result})
            else:
                print(f'  [스킵] 잘못된 ticker 형식: {t!r} ({art["title"][:40]})')

    print(f'  촉매 후보: {len(candidates)}건')
    if not candidates:
        print('  촉매 없음 — 종료')
        return

    # ── 3. 보유종목 제외 + 14일 재탕 제외 + 배치 내 중복 제거 ──────
    fresh = []
    seen_this_batch = set()   # 같은 실행에서 같은 ticker 2번 통과 방지
    for c in candidates:
        t = c['ticker'].upper()
        if t in EXCLUDE_HOLDINGS:
            print(f'  [{t}] 보유종목 발굴 제외')
            continue
        if t in seen_this_batch:
            print(f'  [{t}] 배치 내 중복 — 제외')
            continue
        if _is_recent(t, NOVELTY_DAYS):
            print(f'  [{t}] {NOVELTY_DAYS}일 재탕 제외')
            continue
        c['ticker'] = t
        seen_this_batch.add(t)
        fresh.append(c)

    print(f'  신규 후보: {len(fresh)}건')
    if not fresh:
        print('  신규 후보 없음 — 종료')
        return

    # ── 4. 70B 루브릭 채점 ──────────────────────────────────────────
    bench_price = _get_bench_price('SPY')
    qualified   = []

    for c in fresh:
        ticker      = c['ticker']
        signal_type = c.get('signal_type', '기타')
        headline    = c['title']

        print(f'  [{ticker}] 루브릭 채점 중... ({headline[:50]})')
        rubric = _judge_rubric(headline, ticker, signal_type)
        if rubric is None:
            continue

        # 후행 즉사: 미래성 0점이면 즉시 탈락
        if rubric.get('미래성', 0) == 0:
            print(f'  [{ticker}] 미래성 0점 — 즉시 탈락')
            continue

        total = rubric.get('총점', 0)
        # 신호유형별 임계값 오버라이드
        threshold = _cfg['rubric'].get('threshold_overrides', {}).get(signal_type, RUBRIC_THRESHOLD)
        if total < threshold:
            print(f'  [{ticker}] 총점 {total} < {threshold} — 탈락')
            continue

        print(f'  [{ticker}] ★ 통과! 총점 {total}')
        qualified.append({**c, 'rubric': rubric, 'total': total})

    if not qualified:
        print('✅ 발굴 신호 없음 (0건 정상)')
        return

    # ── 5. 발송 + DB 로깅 ───────────────────────────────────────────
    now_str = datetime.now().strftime('%m/%d %H:%M')
    lines   = [f'<b>🔍 발굴 신호  {now_str}</b>\n']

    for q in qualified:
        ticker      = q['ticker']
        headline    = q['title']
        source      = q['source']
        rubric      = q['rubric']
        total       = q['total']
        signal_type = q.get('signal_type', '기타')
        reasoning   = rubric.get('논리사슬', '')

        price_at = _get_price(ticker)
        if price_at is None:
            print(f'  [{ticker}] 현재가 조회 실패 — DB 저장 건너뜀 (채점 불가)')
            row_id = None
        else:
            row_id = _log_signal_db(
                ticker=ticker, market='US', signal_type=signal_type,
                headline=headline, source=source, reasoning=reasoning,
                rubric_score=total, rubric_detail=rubric,
                price_at_flag=price_at, bench_at_flag=bench_price,
            )

        lines.append(f'<b>{ticker}</b>  [{signal_type}]')
        lines.append(f'{headline}')
        lines.append(f'출처: {source}  |  루브릭 {total}/10')
        lines.append(f'근거: {reasoning[:120]}' if reasoning else '')
        if price_at:
            lines.append(f'현재가 ${price_at:.2f}')
        lines.append(f'<i>DB id: {row_id}</i>' if row_id else '')
        lines.append('')

    lines.append(f'<i>* 루브릭 {RUBRIC_THRESHOLD}점 이상 + 미래성 통과 신호만 발송</i>')
    lines.append('<i>  보유종목 발굴 제외 / 14일 재탕 금지 / 배치 내 중복 제거</i>')

    _send_telegram('\n'.join(l for l in lines if l is not None))
    print(f'✅ 발굴 신호 {len(qualified)}건 발송 완료!')


if __name__ == '__main__':
    run_discovery()
