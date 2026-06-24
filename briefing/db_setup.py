"""
gmcapital.db 초기화 — 신호 로깅 / 결과추적 / 컨설팅 이력
최초 1회 또는 스키마 변경 시 실행
"""
import sqlite3, os

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH  = os.path.join(BASE_DIR, 'gmcapital.db')


def init_db():
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.executescript("""
    PRAGMA journal_mode=WAL;

    -- 발굴 신호 로그
    CREATE TABLE IF NOT EXISTS signals (
        id             INTEGER PRIMARY KEY AUTOINCREMENT,
        flagged_at     TEXT    NOT NULL,          -- ISO8601
        ticker         TEXT    NOT NULL,
        market         TEXT    NOT NULL DEFAULT 'US',  -- US / KR
        signal_type    TEXT,                      -- 설비증설/대규모수주/...
        headline       TEXT,
        source         TEXT,
        reasoning      TEXT,
        rubric_score   INTEGER,                   -- 70B 루브릭 총점 (0-10)
        rubric_detail  TEXT,                      -- JSON 5개 항목 점수
        groq_model     TEXT,
        price_at_flag  REAL,
        benchmark      TEXT    DEFAULT 'SPY',
        bench_at_flag  REAL,
        status         TEXT    DEFAULT 'pending'  -- pending / evaluated / stale
    );

    -- 결과추적 (T+1/3/5/10)
    CREATE TABLE IF NOT EXISTS outcomes (
        signal_id     INTEGER PRIMARY KEY REFERENCES signals(id),
        price_t1      REAL,   price_t3     REAL,
        price_t5      REAL,   price_t10    REAL,
        bench_t5      REAL,
        ret_t1        REAL,   ret_t3       REAL,
        ret_t5        REAL,   ret_t10      REAL,
        alpha_t5      REAL,                       -- ret_t5 - bench_t5
        mfe           REAL,   mae          REAL,  -- max favorable/adverse excursion
        verdict       TEXT,                       -- hit / miss / neutral
        evaluated_at  TEXT
    );

    -- 설정 변경 이력 (컨설팅 레이어 자기개선 추적)
    CREATE TABLE IF NOT EXISTS config_history (
        id          INTEGER PRIMARY KEY AUTOINCREMENT,
        changed_at  TEXT NOT NULL,
        key         TEXT NOT NULL,
        old_value   TEXT,
        new_value   TEXT,
        reason      TEXT
    );

    -- 야간 컨설팅 리포트 이력
    CREATE TABLE IF NOT EXISTS consult_log (
        id           INTEGER PRIMARY KEY AUTOINCREMENT,
        reported_at  TEXT NOT NULL,
        report_text  TEXT,
        suggestions  TEXT,   -- JSON array (≤3건)
        applied      INTEGER DEFAULT 0
    );
    """)
    conn.commit()
    conn.close()
    print(f'DB 초기화 완료: {DB_PATH}')


def get_conn():
    return sqlite3.connect(DB_PATH)


if __name__ == '__main__':
    init_db()
