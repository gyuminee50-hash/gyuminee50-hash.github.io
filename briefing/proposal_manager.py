"""
AP 분석관 — Groq 70B 기반 동적 업그레이드 제안
하드코딩 카테고리/풀 없음.
지금까지 보낸 제안 전체를 Groq에게 넘기고,
AI가 스스로 안 다룬 새 영역을 골라 제안한다.
"""
import json, os
from datetime import datetime, timedelta

BASE_DIR     = os.path.dirname(os.path.abspath(__file__))
STATUS_FILE  = os.path.join(BASE_DIR, 'family_office_status.json')
HISTORY_FILE = os.path.join(BASE_DIR, 'proposal_history.json')

IMPLEMENTED = """\
- 모닝브리핑: RSS 뉴스 수집 → 8B 1차 필터 → 70B 루브릭 채점(5항목) → 발굴신호 발송 (06:30)
- 리스크관리팀: 섹터 편중 50% 경고 + Groq 리스크 분석 (07:00)
- 실수감시팀: FOMO(매수 전 30일 20%+ 급등) + 손절회피(-25% 이하 보유) 패턴 감지 (08:30)
- Regime 엔진: VIX/10년물금리/SPY90일/DXY30일 점수화 → 5단계 국면 + 자산배분 권고 (22:30)
- 투자위원회: 종목별 Bull/Bear/중립 토론 → CEO 판단 요청 (월요일 08:00)
- ROY 대시보드: 발굴신호 결과추적(hit/miss) + 패밀리오피스 현황 (daily push)
- 이상거래량 스캐너: S&P500 전종목 거래량 2σ 이상 감지 (23:00)
- 가격경보: 52주 고저가 근접 즉시 알림
- 어닝 알림, 프리마켓 속보, 주간/월간 포트폴리오 리포트
- 보유종목 홀딩 전용 모니터링 채널"""

_AP_SYSTEM = """\
당신은 GM Capital ROY 시스템의 수석 AP(Analysis & Proposal) 분석관입니다.

CEO 규민님 프로필:
- 계리사 출신 CEO. 미국주식 전문 투자자.
- 보유: TSM(TSMC), MU(Micron), GGLL(Alphabet 2x 레버리지), IEMG(신흥국ETF), SPY, QQQ
- CEO가 이미 직접 파악하는 것: 급등락 종목, 채권금리 방향, 기술적 분석, 선행신호, 차트

역할:
ROY 시스템과 홈페이지를 영구적으로 더 좋게 만드는 기능 추가/개선을 제안한다.
제안이 채택되면 새 자동화 기능이 생기거나, 홈페이지가 개선되거나, 기존 루틴이 더 똑똑해진다.

핵심 임무:
지금까지 제안한 모든 항목을 보고, 아직 한 번도 다루지 않은 완전히 새로운 영역을 스스로 발굴하라.
주제 선택도 AI가 알아서 한다. 정해진 카테고리 없음.

금지:
- 1회성 분석 제안 ("분석해봐", "계산해봐" 류)
- 단순 종목 모니터링 추가
- 이미 구현된 기능 재제안
- 지금까지 제안한 항목과 유사한 내용 (표현만 바꾸는 것 포함)
- CEO가 이미 직접 파악하는 것
- 영어·일본어·약어 사용

필수:
- 모든 내용 한국어로만
- 구현하면 ROY나 홈페이지가 영구적으로 더 좋아지는 것
- CEO가 바로 이해할 수 있는 쉬운 말"""


def _load_fo():
    try:
        with open(STATUS_FILE, 'r', encoding='utf-8') as f:
            return json.load(f)
    except Exception:
        return {}


def _load_history():
    try:
        with open(HISTORY_FILE, 'r', encoding='utf-8') as f:
            return json.load(f)
    except Exception:
        return {}


def _save_history(h):
    with open(HISTORY_FILE, 'w', encoding='utf-8') as f:
        json.dump(h, f, ensure_ascii=False, indent=2)


def _market_snapshot():
    snap = {}
    try:
        import yfinance as yf
        snap['vix'] = round(float(yf.Ticker('^VIX').fast_info.last_price), 2)
    except Exception:
        pass
    try:
        import yfinance as yf
        h = yf.Ticker('SPY').history(period='2d')
        snap['spy_chg'] = round((float(h['Close'].iloc[-1]) / float(h['Close'].iloc[-2]) - 1) * 100, 2)
    except Exception:
        pass
    try:
        import yfinance as yf
        snap['rate10y'] = round(float(yf.Ticker('^TNX').fast_info.last_price), 2)
    except Exception:
        pass
    try:
        import yfinance as yf
        snap['usdkrw'] = int(float(yf.Ticker('USDKRW=X').fast_info.last_price))
    except Exception:
        pass
    return snap


def _build_context(fo, snap):
    now = datetime.now()
    lines = [f"오늘: {now.strftime('%Y-%m-%d')}"]

    mkt = []
    if 'vix' in snap:
        lvl = ('위험' if snap['vix'] > 30 else '경계' if snap['vix'] > 25
               else '주의' if snap['vix'] > 20 else '안정')
        mkt.append(f"VIX {snap['vix']} ({lvl})")
    if 'spy_chg' in snap:
        mkt.append(f"SPY {snap['spy_chg']:+.2f}%")
    if 'rate10y' in snap:
        mkt.append(f"10년물금리 {snap['rate10y']:.2f}%")
    if 'usdkrw' in snap:
        mkt.append(f"달러/원 {snap['usdkrw']}")
    if mkt:
        lines.append("시장: " + " | ".join(mkt))

    r = fo.get('regime', {})
    if r:
        lines.append(f"시장국면: {r.get('regime','—')} ({r.get('score',0):+d}점) → {r.get('alloc','—')}")

    rk = fo.get('risk', {})
    if rk:
        w = rk.get('warnings', [])
        lines.append(f"리스크: {rk.get('risk_level','—')} | 총자산 {rk.get('total_krw',0):,}원"
                     + (f" | 편중경고: {', '.join(w)}" if w else ""))

    mk = fo.get('mistakes', {})
    if mk:
        cnt = mk.get('alert_count', 0)
        lines.append(f"실수감시: {'경고 '+str(cnt)+'건' if cnt else '이상 없음'}")

    c = fo.get('committee', {})
    if c:
        lines.append(f"포트폴리오 수익률: {c.get('total_ret', 0):+.1f}%")

    return '\n'.join(lines)


def _too_similar(new_title, past_titles, recent_n=30):
    """최근 recent_n개 제안 중 하나라도 2글자 이상 겹치는 핵심 단어가 있으면 True."""
    stop = {'분석', '추가', '자동화', '시스템', '서비스', '도구', '실시간', '강화',
            '평가', '최적화', '모니터링', '알림', '제공', '업데이트', '개선', '관리'}
    def _keywords(t):
        return {w for w in t.split() if len(w) >= 2 and w not in stop}

    new_kws = _keywords(new_title)
    if not new_kws:
        return False
    for past in past_titles[:recent_n]:
        shared = new_kws & _keywords(past)
        if len(shared) >= 2:
            return True
    return False


def get_proposals(count=3):
    import groq_client

    fo      = _load_fo()
    history = _load_history()
    snap    = _market_snapshot()

    # 지금까지 제안한 모든 제목 목록 (날짜순 최신 100개)
    sorted_history = sorted(history.items(), key=lambda x: x[1], reverse=True)
    all_past = [title for title, _ in sorted_history[:100]]

    ctx = _build_context(fo, snap)

    past_str = '\n'.join(f"- {t}" for t in all_past) if all_past else "없음 (첫 번째 제안)"

    prompt = f"""{ctx}

이미 구현된 ROY 기능:
{IMPLEMENTED}

지금까지 CEO에게 보낸 업그레이드 제안 전체 목록:
{past_str}

---
위 목록에서 반복 등장하는 핵심 주제(예: 섹터 분석, 중립국면 종목, 자산배분, 수익률 차트, 위험도 평가)는
이미 완전히 소진된 영역이다. 이 영역들은 절대 제안하지 말 것.

위 목록을 분석해서:
1. 어떤 영역이 이미 많이 다뤄졌는지 파악하고 완전히 배제하라
2. 아직 한 번도 제안하지 않은 완전히 새로운 영역(다른 산업, 다른 기술, 다른 워크플로우)을 스스로 발굴하라
3. 그 영역에서 오늘 시장/포트폴리오 상황에 맞는 제안 {count + 2}개를 생성하라 (중복 필터 후 {count}개 선정)

반드시 아래 형식으로 ({count + 2}개 모두):

[제안1]
제목: (20자 이내)
어떤기능: (구체적으로 어떻게 작동하는지 1~2줄)
왜필요한가: (오늘 상황 또는 현재 시스템에서 이게 없어서 생기는 문제 1줄)

[제안2]
제목: ...
어떤기능: ...
왜필요한가: ...

[제안3]
제목: ...
어떤기능: ...
왜필요한가: ...

[제안4]
제목: ...
어떤기능: ...
왜필요한가: ...

[제안5]
제목: ...
어떤기능: ...
왜필요한가: ...

모든 내용 한국어로만. 영어·일본어·약어 금지."""

    try:
        raw = groq_client.call(
            prompt,
            system=_AP_SYSTEM,
            max_tokens=1200,
            temperature=0.85,
            model='qwen/qwen3.8-27b',
        )
    except Exception as e:
        print(f"  [AP 분석관] Groq 오류: {e}")
        return [("AP 분석관 오류", f"Groq 연결 실패: {e}")]

    # 파싱
    proposals = []
    for block in raw.split('[제안'):
        if not block.strip():
            continue
        block_lines = block.splitlines()
        title = next(
            (l.split('제목:')[-1].strip().lstrip(']').strip()
             for l in block_lines if '제목:' in l), None
        )
        if not title:
            first = block_lines[0].strip() if block_lines else ''
            if ']' in first:
                rest = first.split(']', 1)[-1].strip()
                if rest:
                    title = rest.replace('제목:', '').strip()
        what = next(
            (l.split('어떤기능:')[-1].strip() for l in block_lines if '어떤기능:' in l), ''
        )
        why = next(
            (l.split('왜필요한가:')[-1].strip() for l in block_lines if '왜필요한가:' in l), ''
        )
        if title:
            desc = (f"{what} → {why}" if what and why else what or why or '')
            proposals.append((title[:30], desc))

    if not proposals:
        lines = [l.strip() for l in raw.splitlines() if l.strip()]
        for i in range(0, min(count * 2, len(lines)), 2):
            proposals.append((
                lines[i][:30],
                lines[i + 1] if i + 1 < len(lines) else ''
            ))

    # 중복 필터: 최근 30개와 핵심 단어 2개 이상 겹치면 제거
    fresh = []
    for title, desc in proposals:
        if _too_similar(title, all_past, recent_n=30):
            print(f'  [중복 제거] {title}')
        else:
            fresh.append((title, desc))

    selected = fresh[:count]
    # 필터 후 부족하면 원래 목록에서 보충
    if len(selected) < count:
        for p in proposals:
            if p not in selected and len(selected) < count:
                selected.append(p)

    today = datetime.now().strftime('%Y-%m-%d')
    for title, _ in selected:
        history[title] = today
    _save_history(history)

    return selected


def remaining_count():
    return 999
