"""
야간 컨설팅 잡 — 매일 밤 23:00 실행
DB 성적표 분석 → Groq 개선안 ≤3건 생성 → 텔레그램 발송
"""
import json, os, sqlite3, yaml, requests
from datetime import datetime, timedelta, date

import groq_client
from db_setup import DB_PATH, get_conn

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
with open(os.path.join(BASE_DIR, 'config.json'),  'r', encoding='utf-8') as f:
    _api_cfg = json.load(f)
with open(os.path.join(BASE_DIR, 'config.yaml'), 'r', encoding='utf-8') as f:
    _cfg = yaml.safe_load(f)

JUDGE_MODEL = _cfg['models']['judge']
SCOUT_MODEL = _cfg['models']['scout']
DOMAINS     = _cfg['insight']['domains']


def _rolling_stats(days=30):
    """최근 N일 통계 딕셔너리 반환."""
    cutoff = (datetime.now() - timedelta(days=days)).isoformat()
    conn   = get_conn()

    total = conn.execute(
        "SELECT COUNT(*) FROM signals WHERE flagged_at>=?", (cutoff,)
    ).fetchone()[0]

    evaluated = conn.execute(
        "SELECT COUNT(*) FROM signals s JOIN outcomes o ON s.id=o.signal_id WHERE s.flagged_at>=?",
        (cutoff,)
    ).fetchone()[0]

    hit_count = conn.execute(
        "SELECT COUNT(*) FROM signals s JOIN outcomes o ON s.id=o.signal_id "
        "WHERE s.flagged_at>=? AND o.verdict='hit'",
        (cutoff,)
    ).fetchone()[0]

    miss_count = conn.execute(
        "SELECT COUNT(*) FROM signals s JOIN outcomes o ON s.id=o.signal_id "
        "WHERE s.flagged_at>=? AND o.verdict='miss'",
        (cutoff,)
    ).fetchone()[0]

    avg_alpha = conn.execute(
        "SELECT AVG(o.alpha_t5) FROM signals s JOIN outcomes o ON s.id=o.signal_id "
        "WHERE s.flagged_at>=? AND o.alpha_t5 IS NOT NULL",
        (cutoff,)
    ).fetchone()[0]

    avg_rubric = conn.execute(
        "SELECT AVG(rubric_score) FROM signals WHERE flagged_at>=? AND rubric_score IS NOT NULL",
        (cutoff,)
    ).fetchone()[0]

    # 신호유형별 적중률
    type_stats = conn.execute(
        """SELECT s.signal_type, COUNT(*) as cnt,
           SUM(CASE WHEN o.verdict='hit' THEN 1 ELSE 0 END) as hits
           FROM signals s
           LEFT JOIN outcomes o ON s.id=o.signal_id
           WHERE s.flagged_at>=?
           GROUP BY s.signal_type""",
        (cutoff,)
    ).fetchall()

    conn.close()

    hit_rate = (hit_count / evaluated * 100) if evaluated > 0 else 0

    return {
        'days': days,
        'total': total,
        'evaluated': evaluated,
        'hit_count': hit_count,
        'miss_count': miss_count,
        'hit_rate': round(hit_rate, 1),
        'avg_alpha': round(avg_alpha, 2) if avg_alpha else None,
        'avg_rubric': round(avg_rubric, 1) if avg_rubric else None,
        'type_stats': [{'type': r[0], 'count': r[1], 'hits': r[2] or 0} for r in type_stats],
    }


def _recent_signals(n=5):
    """최근 N개 신호 요약 (컨설팅 컨텍스트용)."""
    conn  = get_conn()
    rows  = conn.execute(
        """SELECT s.ticker, s.signal_type, s.headline, s.rubric_score, o.verdict, o.ret_t5, o.alpha_t5
           FROM signals s
           LEFT JOIN outcomes o ON s.id=o.signal_id
           ORDER BY s.flagged_at DESC LIMIT ?""",
        (n,)
    ).fetchall()
    conn.close()
    return [{'ticker':r[0],'type':r[1],'headline':r[2],'rubric':r[3],
             'verdict':r[4],'ret5':r[5],'alpha5':r[6]} for r in rows]


_CONSULT_PROMPT = """\
GM Capital ROY 발굴 시스템의 최근 {days}일 성적표:

총 신호: {total}건
채점완료: {evaluated}건
적중률: {hit_rate}%  (기준: T+5 수익 3%+)
평균 alpha: {avg_alpha}%
평균 루브릭 점수: {avg_rubric}/10

최근 신호 샘플:
{recent}

위 데이터를 분석해서 시스템 개선안을 3건 이내로 제안하라.
각 개선안은 구체적 실행 방법을 포함해야 한다.
형식:
개선안1: [제목] — 구체적 내용
개선안2: [제목] — 구체적 내용
개선안3: [제목] — 구체적 내용 (없으면 생략)

데이터가 부족하면 (채점 5건 미만) "데이터 부족 — 1주 후 재분석" 으로만 답변"""

_INSIGHT_PROMPT = """\
도메인: {domain}
분석 날짜: {today}

{domain} 관점에서 GM Capital의 AI 발굴 시스템에 중요한 트렌드나 리스크가 있는가?
있다면 1~2문장으로, 없다면 "변화 없음"으로만 답변. 50자 이내."""


def _send_telegram(text):
    token   = _api_cfg['telegram_token']
    chat_id = _api_cfg['telegram_chat_id']
    requests.post(
        f'https://api.telegram.org/bot{token}/sendMessage',
        json={'chat_id': chat_id, 'text': text, 'parse_mode': 'HTML'},
        timeout=15,
    )


def _log_consult(report_text, suggestions):
    try:
        conn = get_conn()
        conn.execute(
            "INSERT INTO consult_log (reported_at, report_text, suggestions) VALUES (?,?,?)",
            (datetime.now().isoformat(), report_text,
             json.dumps(suggestions, ensure_ascii=False))
        )
        conn.commit()
        conn.close()
    except Exception as e:
        print(f'  [컨설팅 로그 오류] {e}')


def run_consult():
    """야간 컨설팅 리포트 생성 + 발송."""
    print(f'[{datetime.now().strftime("%Y-%m-%d %H:%M")}] 야간 컨설팅 시작...')

    stats   = _rolling_stats(30)
    recent  = _recent_signals(5)
    today   = date.today().isoformat()

    # ── 성적표 리포트 ────────────────────────────────────────────────
    recent_str = '\n'.join(
        f"- {r['ticker']} [{r['type']}] 루브릭{r['rubric']} "
        f"→ {r['verdict'] or 'pending'} "
        f"(T+5={r['ret5']:.1f}% alpha={r['alpha5']:.1f}%)"
        if r['ret5'] is not None else
        f"- {r['ticker']} [{r['type']}] 루브릭{r['rubric']} → 채점대기"
        for r in recent
    )

    # ── 개선안 생성 (70B) ────────────────────────────────────────────
    suggestions = []
    try:
        raw = groq_client.call(
            _CONSULT_PROMPT.format(
                days=stats['days'],
                total=stats['total'],
                evaluated=stats['evaluated'],
                hit_rate=stats['hit_rate'],
                avg_alpha=stats['avg_alpha'] if stats['avg_alpha'] is not None else 'N/A',
                avg_rubric=stats['avg_rubric'] if stats['avg_rubric'] is not None else 'N/A',
                recent=recent_str or '(아직 신호 없음)',
            ),
            max_tokens=400, temperature=0.3,
            model=JUDGE_MODEL,
        )
        for line in raw.strip().splitlines():
            if line.startswith('개선안'):
                suggestions.append(line)
    except Exception as e:
        print(f'  [Groq 오류] {e}')
        suggestions = ['개선안 생성 실패']

    # ── 도메인 인사이트 (8B, 최대 2개) ─────────────────────────────
    domain_insights = []
    for domain in DOMAINS[:3]:  # 토큰 절약: 3개만
        try:
            raw = groq_client.call(
                _INSIGHT_PROMPT.format(domain=domain, today=today),
                max_tokens=80, temperature=0.2,
                model=SCOUT_MODEL,
            )
            if '변화 없음' not in raw:
                domain_insights.append(f'[{domain}] {raw.strip()}')
        except Exception:
            pass

    # ── 텔레그램 발송 ────────────────────────────────────────────────
    lines = [
        f'<b>✅ ROY 야간 컨설팅  {today}</b>',
        '',
        f'<b>📊 최근 {stats["days"]}일 성적</b>',
        f'신호 {stats["total"]}건  채점 {stats["evaluated"]}건',
        f'적중률 {stats["hit_rate"]}%  avg alpha {stats["avg_alpha"]}%',
        f'avg 루브릭 {stats["avg_rubric"]}/10',
        '',
    ]

    if suggestions:
        lines.append('<b>🔬 개선안</b>')
        lines.extend(suggestions)
        lines.append('')

    if domain_insights:
        lines.append('<b>🌐 도메인 인사이트</b>')
        lines.extend(domain_insights)
        lines.append('')

    lines.append('<i>* ROY 자가진화 컨설팅 — 승인/보류 여부를 알려주세요</i>')

    report_text = '\n'.join(lines)
    _send_telegram(report_text)
    _log_consult(report_text, suggestions)
    print(f'✅ 야간 컨설팅 발송 완료 (개선안 {len(suggestions)}건)')


if __name__ == '__main__':
    run_consult()
