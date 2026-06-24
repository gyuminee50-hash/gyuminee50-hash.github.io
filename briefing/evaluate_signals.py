"""
결과추적 잡 — T+1/3/5/10 주가 채점
매일 1회 실행 (예: 23:00). 데이터는 신호 발송 다음 날부터 쌓임.
"""
import json, os, sqlite3, yaml
from datetime import datetime, timedelta, date

import yfinance as yf
from db_setup import DB_PATH, get_conn

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
with open(os.path.join(BASE_DIR, 'config.yaml'), 'r', encoding='utf-8') as f:
    _cfg = yaml.safe_load(f)

HIT_RET  = _cfg['outcome']['hit_return_pct']   # 3.0%
MISS_RET = _cfg['outcome']['miss_return_pct']  # 0.0%


def _biz_offset(from_date, n):
    """from_date에서 n 영업일 후 날짜 반환 (간단한 방식, 미국 공휴일 미제외)."""
    d = from_date
    count = 0
    while count < n:
        d += timedelta(days=1)
        if d.weekday() < 5:  # 월~금
            count += 1
    return d


def _price_on(ticker, target_date):
    """해당 날짜의 종가. 없으면 None."""
    start = target_date - timedelta(days=5)
    end   = target_date + timedelta(days=2)
    try:
        df = yf.download(ticker, start=start, end=end, interval='1d',
                         auto_adjust=True, progress=False)
        if df.empty:
            return None
        closest = df.index[df.index <= str(target_date)]
        if len(closest) == 0:
            return None
        return float(df.loc[closest[-1], 'Close'])
    except Exception:
        return None


def evaluate_pending_signals():
    """pending 신호 중 T+1/3/5/10 날짜가 지난 것 채점."""
    today = date.today()
    conn  = get_conn()
    rows  = conn.execute(
        "SELECT id, flagged_at, ticker, price_at_flag, benchmark, bench_at_flag "
        "FROM signals WHERE status='pending'"
    ).fetchall()
    print(f'[결과추적] pending 신호 {len(rows)}건 확인')

    updated = 0
    for row in rows:
        sig_id, flagged_at, ticker, price_flag, benchmark, bench_flag = row
        try:
            flag_date = datetime.fromisoformat(flagged_at).date()
        except Exception:
            continue

        t1  = _biz_offset(flag_date, 1)
        t3  = _biz_offset(flag_date, 3)
        t5  = _biz_offset(flag_date, 5)
        t10 = _biz_offset(flag_date, 10)

        # T+5 기준으로 채점 가능 여부 확인
        if today < t5:
            print(f'  [{ticker}] T+5 미도래 — 건너뜀')
            continue

        print(f'  [{ticker}] 채점 중...')

        p1  = _price_on(ticker, t1)
        p3  = _price_on(ticker, t3)
        p5  = _price_on(ticker, t5)
        p10 = _price_on(ticker, t10) if today >= t10 else None
        pb5 = _price_on(benchmark or 'SPY', t5) if benchmark else None

        def _ret(p):
            if p is None or price_flag is None or price_flag == 0:
                return None
            return (p - price_flag) / price_flag * 100

        def _bret(p):
            if p is None or bench_flag is None or bench_flag == 0:
                return None
            return (p - bench_flag) / bench_flag * 100

        ret1  = _ret(p1)
        ret3  = _ret(p3)
        ret5  = _ret(p5)
        ret10 = _ret(p10)
        bret5 = _bret(pb5)
        alpha5 = (ret5 - bret5) if (ret5 is not None and bret5 is not None) else None

        # hit/miss/neutral 판정
        if ret5 is None:
            verdict = 'unknown'
        elif ret5 >= HIT_RET:
            verdict = 'hit'
        elif ret5 <= -HIT_RET:
            verdict = 'miss'
        else:
            verdict = 'neutral'

        # MFE/MAE: T+1~T+5 중 최고/최저
        candidates = [r for r in [ret1, ret3, ret5] if r is not None]
        mfe = max(candidates) if candidates else None
        mae = min(candidates) if candidates else None

        # outcomes 행 upsert
        conn.execute("DELETE FROM outcomes WHERE signal_id=?", (sig_id,))
        conn.execute("""INSERT INTO outcomes
            (signal_id, price_t1,price_t3,price_t5,price_t10,bench_t5,
             ret_t1,ret_t3,ret_t5,ret_t10,alpha_t5,mfe,mae,verdict,evaluated_at)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (sig_id, p1,p3,p5,p10,pb5,
             ret1,ret3,ret5,ret10,alpha5,mfe,mae,verdict,
             datetime.now().isoformat()))

        # signals 상태 업데이트
        conn.execute("UPDATE signals SET status='evaluated' WHERE id=?", (sig_id,))
        conn.commit()

        print(f'  [{ticker}] 채점 완료: T+5={ret5:.1f}% alpha={alpha5:.1f}% → {verdict}' if ret5 is not None else f'  [{ticker}] 가격 데이터 없음')
        updated += 1

    conn.close()
    print(f'[결과추적] {updated}건 채점 완료')


if __name__ == '__main__':
    evaluate_pending_signals()
