"""
GM Capital 오전 9시 — 부서별 실제 활동 보고
DAT: 실제 주가·등락률 / INF: RSS 수집 현황 / AP: 가격 경보 / DES: 홈페이지 업데이트
"""
import requests, json, os, sys, subprocess
import feedparser
from datetime import datetime, timedelta
import time as _time

sys.stdout.reconfigure(encoding='utf-8')

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
REPO_DIR = os.path.normpath(os.path.join(BASE_DIR, '..'))

with open(os.path.join(BASE_DIR, 'config.json'), 'r', encoding='utf-8') as f:
    config = json.load(f)

TOKEN   = config['telegram_token']
CHAT_ID = config['telegram_chat_id']

HOLDINGS = [
    ('TSM',  'TSMC'),
    ('MU',   'Micron'),
    ('GGLL', 'Alphabet 2x'),
    ('IEMG', '신흥국 ETF'),
    ('SPY',  'S&P500'),
]

KOREAN_FEEDS = [
    ('연합뉴스',   'https://www.yna.co.kr/rss/economy.xml'),
    ('한국경제',   'https://www.hankyung.com/rss/feed_economy.xml'),
    ('이데일리',   'https://rss2.edaily.co.kr/economy.xml'),
    ('머니투데이', 'https://rss.mt.co.kr/mt_economy.xml'),
]

US_FEEDS = [
    ('Reuters',       'https://feeds.reuters.com/reuters/businessNews'),
    ('Yahoo Finance', 'https://finance.yahoo.com/rss/topfinstories'),
    ('CNBC',          'https://search.cnbc.com/rs/search/combinedcms/view.xml?partnerId=wrss01&id=100003114'),
    ('MarketWatch',   'https://feeds.content.dowjones.io/public/rss/mw_topstories'),
]


def get_vix():
    """VIX 공포지수 조회"""
    try:
        import yfinance as yf
        hist = yf.Ticker('^VIX').history(period='2d')
        val  = float(hist['Close'].iloc[-1])
        if val < 15:   state = '😌 안정'
        elif val < 20: state = '🙂 보통 이하'
        elif val < 25: state = '😐 보통'
        elif val < 30: state = '😟 경계'
        else:          state = '😱 위험'
        return {'value': round(val, 2), 'state': state}
    except Exception as e:
        print(f'  [VIX 조회 실패] {e}')
        return None


def get_fear_greed():
    """CNN 공포탐욕지수 조회"""
    try:
        url = 'https://production.dataviz.cnn.io/index/fearandgreed/graphdata'
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36',
            'Accept': 'application/json, text/plain, */*',
            'Referer': 'https://edition.cnn.com/',
            'Origin':  'https://edition.cnn.com',
        }
        r   = requests.get(url, timeout=15, headers=headers)
        obj = r.json()
        cur = round(obj['fear_and_greed']['score'])
        rating_map = {
            'extreme fear': '극단적 공포', 'fear': '공포',
            'neutral': '중립', 'greed': '탐욕', 'extreme greed': '극단적 탐욕'
        }
        label = rating_map.get(obj['fear_and_greed']['rating'].lower(), obj['fear_and_greed']['rating'])
        prev  = None
        hist  = obj.get('fear_and_greed_historical', {})
        if hist.get('previous_close'):
            prev = round(hist['previous_close']['score'])
        return {'value': cur, 'label': label, 'prev': prev}
    except Exception as e:
        print(f'  [F&G 조회 실패] {e}')
        return None


def get_portfolio():
    """실제 주가 데이터 조회"""
    try:
        import yfinance as yf
        syms = [h[0] for h in HOLDINGS]
        data = yf.download(syms, period='2d', progress=False, auto_adjust=True)['Close']
        results = []
        for sym, name in HOLDINGS:
            try:
                cur  = float(data[sym].iloc[-1])
                prev = float(data[sym].iloc[-2])
                chg  = (cur - prev) / prev * 100
                results.append({'sym': sym, 'name': name, 'price': cur, 'chg': chg})
            except:
                pass
        return results
    except Exception as e:
        print(f'  [주가 조회 실패] {e}')
        return []


def count_articles():
    """RSS 피드에서 최근 24시간 기사 수 집계"""
    cutoff = datetime.now() - timedelta(hours=24)
    kr_count, kr_sources = 0, []
    us_count, us_sources = 0, []

    for source, url in KOREAN_FEEDS:
        try:
            feed = feedparser.parse(url, request_headers={'User-Agent': 'Mozilla/5.0'})
            for e in feed.entries[:40]:
                for field in ('published_parsed', 'updated_parsed'):
                    parsed = e.get(field)
                    if parsed:
                        try:
                            pub = datetime.fromtimestamp(_time.mktime(parsed))
                            if pub >= cutoff:
                                kr_count += 1
                                if source not in kr_sources:
                                    kr_sources.append(source)
                        except:
                            pass
                        break
        except:
            pass

    for source, url in US_FEEDS:
        try:
            feed = feedparser.parse(url, request_headers={'User-Agent': 'Mozilla/5.0'})
            for e in feed.entries[:40]:
                for field in ('published_parsed', 'updated_parsed'):
                    parsed = e.get(field)
                    if parsed:
                        try:
                            pub = datetime.fromtimestamp(_time.mktime(parsed))
                            if pub >= cutoff:
                                us_count += 1
                                if source not in us_sources:
                                    us_sources.append(source)
                        except:
                            pass
                        break
        except:
            pass

    return kr_count, kr_sources, us_count, us_sources


def get_alert_status():
    """오늘 발생한 가격 경보 확인"""
    alert_file = os.path.join(BASE_DIR, 'price_alert_log.json')
    if not os.path.exists(alert_file):
        return []
    with open(alert_file, 'r', encoding='utf-8') as f:
        log = json.load(f)
    today = datetime.now().strftime('%Y-%m-%d')
    return [k for k, v in log.items() if v == today]


def push_activity_log(log_data):
    """활동 로그 JSON 저장 + GitHub push → 홈페이지 자동 업데이트"""
    log_path = os.path.join(REPO_DIR, 'activity_log.json')
    with open(log_path, 'w', encoding='utf-8') as f:
        json.dump(log_data, f, ensure_ascii=False, indent=2)
    try:
        subprocess.run(['git', '-C', REPO_DIR, 'add', 'activity_log.json'],
                       check=True, capture_output=True)
        subprocess.run(['git', '-C', REPO_DIR, 'commit', '-m',
                        f'활동 로그 {log_data["updated"]}'],
                       check=True, capture_output=True)
        subprocess.run(['git', '-C', REPO_DIR, 'push'],
                       check=True, capture_output=True)
        print(f'  [DES] 홈페이지 활동 로그 업로드 완료')
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
    # 09:00 전용
    if now.hour >= 10:
        print(f'[{now:%Y-%m-%d %H:%M}] meeting_report는 09시 전용. 스킵.')
        return

    day_kr   = ['월','화','수','목','금','토','일'][now.weekday()]
    date_str = f"{now.strftime('%Y년 %m월 %d일')} ({day_kr}요일)"

    print('  [DAT] 주가 조회 중...')
    portfolio = get_portfolio()

    print('  [INF] 기사 수집 현황 집계 중...')
    kr_count, kr_sources, us_count, us_sources = count_articles()

    print('  [AP] 가격 경보 확인 중...')
    alerts = get_alert_status()

    print('  [DAT] VIX + 공포탐욕지수 조회 중...')
    vix = get_vix()
    fg  = get_fear_greed()

    # 활동 로그 저장 + 홈페이지 push
    log_data = {
        'updated':   now.strftime('%Y-%m-%d %H:%M'),
        'portfolio': [{'sym': p['sym'], 'name': p['name'],
                       'price': round(p['price'], 2), 'chg': round(p['chg'], 2)}
                      for p in portfolio],
        'inf': {
            'kr_count': kr_count, 'kr_sources': kr_sources,
            'us_count': us_count, 'us_sources': us_sources,
        },
        'ap':  {'alerts': alerts},
        'vix': vix,
        'fg':  fg,
    }
    push_activity_log(log_data)

    # ── 텔레그램 보고 ──
    msg  = f"💼 <b>GM Capital 09:00 부서 보고</b>\n"
    msg += f"{date_str}\n\n"

    # DAT — 실제 주가
    msg += "📊 <b>DAT — 보유 종목 현황</b>\n"
    msg += "─────────────────\n"
    if portfolio:
        for p in portfolio:
            sign = '+' if p['chg'] >= 0 else ''
            arrow = '▲' if p['chg'] >= 0 else '▼'
            msg += f"  {p['sym']:<5} {arrow} <b>{sign}{p['chg']:.2f}%</b>  ${p['price']:.2f}\n"
        avg = sum(p['chg'] for p in portfolio) / len(portfolio)
        sign = '+' if avg >= 0 else ''
        msg += f"\n  포트폴리오 평균: <b>{sign}{avg:.2f}%</b>\n"
    else:
        msg += "  주가 조회 실패 (장 마감 후 또는 네트워크 오류)\n"

    # INF — 실제 수집 현황
    msg += "\n📡 <b>INF — 기사 수집 현황</b> (24시간)\n"
    msg += "─────────────────\n"
    msg += f"  한국: <b>{kr_count}건</b>"
    if kr_sources:
        msg += f"  ({', '.join(kr_sources)})"
    msg += "\n"
    msg += f"  미국: <b>{us_count}건</b>"
    if us_sources:
        msg += f"  ({', '.join(us_sources)})"
    msg += "\n"
    msg += f"  → 모닝 브리핑 후보 풀 준비 완료\n"

    # AP — 경보 현황
    msg += "\n🔍 <b>AP — 가격 경보 점검</b>\n"
    msg += "─────────────────\n"
    if alerts:
        for a in alerts:
            parts = a.split('_52') if '_52' in a else [a, '']
            sym   = parts[0]
            kind  = '52주 고가 근접' if 'high' in a else '52주 저가 근접' if 'low' in a else a
            msg += f"  ⚠️ {sym}: {kind}\n"
    else:
        msg += "  전 종목 이상 없음\n"

    # DES — 홈페이지
    msg += "\n🎨 <b>DES — 홈페이지 업데이트</b>\n"
    msg += "─────────────────\n"
    msg += f"  활동 로그 업로드 완료 ({now.strftime('%H:%M')})\n"

    # 업그레이드 제안
    from proposal_manager import get_proposals
    proposals = get_proposals(count=3)
    msg += "\n💡 <b>오늘의 업그레이드 제안</b>\n"
    msg += "─────────────────\n"
    msg += "<i>원하시는 번호를 ROY에게 전달하면 바로 실행합니다</i>\n\n"
    for i, (title, desc) in enumerate(proposals, 1):
        msg += f"<b>{i}. {title}</b>\n<i>{desc}</i>\n\n"

    msg += "─────────────────\n"
    msg += "🏢 <i>GM Capital · ROY</i>"

    send(msg)
    print(f"[{now:%Y-%m-%d %H:%M}] 09:00 부서 보고 전송 완료")


if __name__ == '__main__':
    main()
