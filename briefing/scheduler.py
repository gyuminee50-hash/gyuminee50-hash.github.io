"""
GM Capital 통합 스케줄러
시작 프로그램 1개로 전체 자동화 관리
24시간 상시 가동 전용
"""
import subprocess, sys, os, time, json, threading
from datetime import datetime, date, timedelta

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
PYTHON   = sys.executable
STATE    = os.path.join(BASE_DIR, 'scheduler_state.json')

# ── 실행 기록 (오늘 이미 실행했는지 추적) ────────────────────────────
def _load_state():
    try:
        with open(STATE, 'r') as f:
            s = json.load(f)
        if s.get('date') != date.today().isoformat():
            return {'date': date.today().isoformat(), 'done': []}
        return s
    except Exception:
        return {'date': date.today().isoformat(), 'done': []}

def _mark_done(job_id):
    s = _load_state()
    if job_id not in s['done']:
        s['done'].append(job_id)
    with open(STATE, 'w') as f:
        json.dump(s, f)

def _already_done(job_id):
    return job_id in _load_state()['done']

# ── 스크립트 실행 ────────────────────────────────────────────────────
def _run(script, job_id):
    if _already_done(job_id):
        return
    print(f'[{datetime.now().strftime("%H:%M")}] 실행: {script}')
    try:
        subprocess.Popen(
            [PYTHON, os.path.join(BASE_DIR, script)],
            cwd=BASE_DIR,
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
        _mark_done(job_id)
    except Exception as e:
        print(f'  [오류] {script}: {e}')

# ── 격주 일요일 체크 ─────────────────────────────────────────────────
def _is_research_day():
    research_state = os.path.join(BASE_DIR, 'research_state.json')
    try:
        with open(research_state, 'r') as f:
            s = json.load(f)
        last = date.fromisoformat(s.get('last_date', '2000-01-01'))
        return (date.today() - last).days >= 14
    except Exception:
        return True   # 파일 없으면 실행

# ── 스케줄 정의 ──────────────────────────────────────────────────────
# (시, 분, 스크립트, job_id, 조건함수|None)
SCHEDULES = [
    (6,  30, 'morning_briefing.py',  'morning',        None),
    (7,  30, 'earnings_alert.py',    'earnings',       None),
    (7,   0, 'weekly_portfolio.py',  'weekly',         lambda: datetime.today().weekday() == 0),  # 월요일
    (8,   0, 'research_report.py',   'research',       lambda: datetime.today().weekday() == 6 and _is_research_day()),  # 격주 일요일
    (22, 30, 'macro_briefing.py',    'macro_2230',     None),
    (4,   0, 'macro_briefing.py',    'macro_0400',     None),
    # 이상 거래량: 23~06시 매 정시
    (23,  0, 'volume_scanner.py',    'scan_2300',      None),
    (0,   0, 'volume_scanner.py',    'scan_0000',      None),
    (1,   0, 'volume_scanner.py',    'scan_0100',      None),
    (2,   0, 'volume_scanner.py',    'scan_0200',      None),
    (3,   0, 'volume_scanner.py',    'scan_0300',      None),
    (4,   0, 'volume_scanner.py',    'scan_0400',      None),
    (5,   0, 'volume_scanner.py',    'scan_0500',      None),
    (6,   0, 'volume_scanner.py',    'scan_0600',      None),
]

# ── 투자일지 watchdog (별도 스레드, 항상 실행) ───────────────────────
def _start_journal():
    try:
        subprocess.Popen(
            [PYTHON, os.path.join(BASE_DIR, 'investment_journal.py')],
            cwd=BASE_DIR,
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
        print('  투자일지 watchdog 시작')
    except Exception as e:
        print(f'  [투자일지 오류] {e}')

# ── 메인 루프 ────────────────────────────────────────────────────────
def main():
    print(f'GM Capital 통합 스케줄러 시작 — {datetime.now().strftime("%Y-%m-%d %H:%M")}')

    # 투자일지 watchdog 먼저 시작
    _start_journal()

    while True:
        now  = datetime.now()
        h, m = now.hour, now.minute

        if m == 0:   # 매 정시에만 체크
            today_str = date.today().isoformat()

            # 날짜 바뀌면 실행 기록 초기화
            s = _load_state()
            if s['date'] != today_str:
                with open(STATE, 'w') as f:
                    json.dump({'date': today_str, 'done': []}, f)

            for sched_h, sched_m, script, job_id, condition in SCHEDULES:
                if h == sched_h and m == sched_m:
                    if condition is None or condition():
                        _run(script, job_id)

        time.sleep(30)   # 30초마다 체크

if __name__ == '__main__':
    main()
