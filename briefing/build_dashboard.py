"""
대시보드 빌더 — DB → JSON → dashboard.html 주입 → git push
매일 결과추적 잡 완료 후 실행 (evaluate_signals.py 연계).
"""
import json, os, sqlite3, subprocess
from datetime import datetime, timedelta, date

from db_setup import DB_PATH, get_conn

BASE_DIR       = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT      = os.path.join(BASE_DIR, '..')
DASHBOARD_HTML = os.path.join(REPO_ROOT, 'dashboard.html')
STATUS_FILE    = os.path.join(BASE_DIR, 'family_office_status.json')


def _load_fo_status():
    """패밀리오피스 팀 최신 결과 JSON 로드."""
    try:
        with open(STATUS_FILE, 'r', encoding='utf-8') as f:
            return json.load(f)
    except Exception:
        return {}


def _build_stats():
    """DB에서 대시보드용 통계 JSON 생성."""
    conn = get_conn()

    # 전체 통계
    total = conn.execute("SELECT COUNT(*) FROM signals").fetchone()[0]
    evaluated = conn.execute(
        "SELECT COUNT(*) FROM signals s JOIN outcomes o ON s.id=o.signal_id"
    ).fetchone()[0]
    hits = conn.execute(
        "SELECT COUNT(*) FROM outcomes WHERE verdict='hit'"
    ).fetchone()[0]
    misses = conn.execute(
        "SELECT COUNT(*) FROM outcomes WHERE verdict='miss'"
    ).fetchone()[0]

    avg_alpha = conn.execute(
        "SELECT AVG(alpha_t5) FROM outcomes WHERE alpha_t5 IS NOT NULL"
    ).fetchone()[0]

    avg_rubric = conn.execute(
        "SELECT AVG(rubric_score) FROM signals WHERE rubric_score IS NOT NULL"
    ).fetchone()[0]

    # 최근 30일 트렌드 (날짜별 신호 수)
    trend_rows = conn.execute(
        """SELECT date(flagged_at) as d, COUNT(*) as cnt
           FROM signals
           WHERE flagged_at >= date('now', '-30 days')
           GROUP BY d ORDER BY d""",
    ).fetchall()

    # 신호유형별 집계
    type_rows = conn.execute(
        """SELECT signal_type, COUNT(*) as cnt,
           SUM(CASE WHEN o.verdict='hit' THEN 1 ELSE 0 END) as hits
           FROM signals s LEFT JOIN outcomes o ON s.id=o.signal_id
           GROUP BY signal_type""",
    ).fetchall()

    # 최근 신호 20건
    recent_rows = conn.execute(
        """SELECT s.flagged_at, s.ticker, s.signal_type, s.headline,
                  s.rubric_score, o.verdict, o.ret_t5, o.alpha_t5
           FROM signals s LEFT JOIN outcomes o ON s.id=o.signal_id
           ORDER BY s.flagged_at DESC LIMIT 20""",
    ).fetchall()

    conn.close()

    hit_rate = round(hits / evaluated * 100, 1) if evaluated > 0 else 0

    return {
        'generated_at': datetime.now().isoformat(),
        'summary': {
            'total_signals': total,
            'evaluated': evaluated,
            'hit_count': hits,
            'miss_count': misses,
            'hit_rate_pct': hit_rate,
            'avg_alpha_pct': round(avg_alpha, 2) if avg_alpha else None,
            'avg_rubric': round(avg_rubric, 1) if avg_rubric else None,
        },
        'trend': [{'date': r[0], 'count': r[1]} for r in trend_rows],
        'by_type': [{'type': r[0] or '기타', 'count': r[1], 'hits': r[2] or 0} for r in type_rows],
        'recent': [
            {
                'date': r[0][:10],
                'ticker': r[1],
                'type': r[2],
                'headline': r[3],
                'rubric': r[4],
                'verdict': r[5] or 'pending',
                'ret5': round(r[6], 1) if r[6] is not None else None,
                'alpha5': round(r[7], 1) if r[7] is not None else None,
            }
            for r in recent_rows
        ],
    }


def build_dashboard():
    """stats JSON을 dashboard.html에 주입 후 git push."""
    if not os.path.exists(DB_PATH):
        print('[대시보드] DB 없음 — 건너뜀')
        return

    stats        = _build_stats()
    stats['fo']  = _load_fo_status()   # 패밀리오피스 팀 현황 포함
    stats_js     = json.dumps(stats, ensure_ascii=False, indent=2)

    # dashboard.html 템플릿 읽기
    if not os.path.exists(DASHBOARD_HTML):
        print('[대시보드] dashboard.html 없음 — 건너뜀')
        return

    with open(DASHBOARD_HTML, 'r', encoding='utf-8') as f:
        html = f.read()

    # __DASHBOARD_DATA__ 플레이스홀더 교체
    marker = '/* __DASHBOARD_DATA__ */'
    new_block = f'/* __DASHBOARD_DATA__ */\nconst STATS = {stats_js};'
    if marker in html:
        import re
        html = re.sub(
            r'/\* __DASHBOARD_DATA__ \*/.*?;',
            new_block,
            html,
            flags=re.DOTALL,
        )
    else:
        html = html.replace('</script>', f'\n{new_block}\n</script>', 1)

    with open(DASHBOARD_HTML, 'w', encoding='utf-8') as f:
        f.write(html)

    print(f'[대시보드] 데이터 주입 완료 (신호 {stats["summary"]["total_signals"]}건)')

    # git push
    try:
        subprocess.run(['git', 'add', 'dashboard.html'], cwd=REPO_ROOT,
                       capture_output=True, check=True)
        subprocess.run(['git', 'commit', '-m',
                        f'dashboard: 결과추적 업데이트 {date.today().isoformat()}'],
                       cwd=REPO_ROOT, capture_output=True, check=True)
        subprocess.run(['git', 'push'], cwd=REPO_ROOT,
                       capture_output=True, check=True)
        print('[대시보드] git push 완료')
    except subprocess.CalledProcessError as e:
        stdout = e.stdout.decode(errors='ignore') if e.stdout else ''
        # 변경사항 없으면 정상
        if 'nothing to commit' in stdout or 'nothing added' in stdout:
            print('[대시보드] 변경사항 없음 — push 생략')
        else:
            print(f'[대시보드] git 오류: {e.stderr.decode(errors="ignore")}')


if __name__ == '__main__':
    build_dashboard()
