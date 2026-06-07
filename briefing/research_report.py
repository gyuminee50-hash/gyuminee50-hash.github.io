"""
격주 AI 리서치 리포트 (PDF)
2주마다 일요일, 포트폴리오 종목 순환 분석
뉴스 + 주가 + 애널리스트 데이터 → Groq 분석 → PDF → 텔레그램 전송
"""
import json, os, re, requests, xml.etree.ElementTree as ET
from datetime import datetime, timedelta, date
from io import BytesIO

import yfinance as yf
import groq_client
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import mm
from reportlab.lib import colors
from reportlab.platypus import (SimpleDocTemplate, Paragraph, Spacer,
                                 Table, TableStyle, HRFlowable)
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
with open(os.path.join(BASE_DIR, 'config.json'), 'r', encoding='utf-8') as f:
    _cfg = json.load(f)

# 종목 순환 리스트 (격주 순서)
ROTATION = [
    {'name': 'TSMC',   'symbol': 'TSM',   'news_query': 'TSMC semiconductor AI'},
    {'name': 'Micron', 'symbol': 'MU',    'news_query': 'Micron Technology memory'},
    {'name': 'GGLL',   'symbol': 'GGLL',  'news_query': 'Alphabet Google GOOGL stock'},
    {'name': 'IEMG',   'symbol': 'IEMG',  'news_query': 'iShares emerging markets ETF'},
    {'name': 'S&P500', 'symbol': 'SPY',   'news_query': 'S&P 500 market outlook'},
    {'name': 'QQQ',    'symbol': 'QQQ',   'news_query': 'Nasdaq QQQ tech ETF'},
]

STATE_FILE = os.path.join(BASE_DIR, 'research_state.json')


# ── 상태 관리 (어떤 종목 차례인지 추적) ──────────────────────────────
def _load_state():
    if os.path.exists(STATE_FILE):
        with open(STATE_FILE, 'r', encoding='utf-8') as f:
            return json.load(f)
    return {'last_index': -1, 'last_date': ''}


def _save_state(index):
    with open(STATE_FILE, 'w', encoding='utf-8') as f:
        json.dump({'last_index': index,
                   'last_date': date.today().isoformat()}, f)


def _get_current_stock():
    state = _load_state()
    next_index = (state['last_index'] + 1) % len(ROTATION)
    return ROTATION[next_index], next_index


# ── 한글 폰트 등록 ────────────────────────────────────────────────────
def _register_font():
    font_candidates = [
        r'C:\Windows\Fonts\malgun.ttf',       # 맑은 고딕
        r'C:\Windows\Fonts\gulim.ttc',         # 굴림
        r'C:\Windows\Fonts\NanumGothic.ttf',
    ]
    for path in font_candidates:
        if os.path.exists(path):
            try:
                pdfmetrics.registerFont(TTFont('Korean', path))
                return 'Korean'
            except Exception:
                continue
    return 'Helvetica'  # fallback (한글 깨질 수 있음)


# ── 데이터 수집 ──────────────────────────────────────────────────────
def get_price_data(symbol):
    """4주 주가 데이터 + 주요 지표"""
    try:
        tk   = yf.Ticker(symbol)
        hist = tk.history(period='1mo')
        info = tk.fast_info

        if hist.empty:
            return None

        start_price = hist['Close'].iloc[0]
        end_price   = hist['Close'].iloc[-1]
        high_4w     = hist['High'].max()
        low_4w      = hist['Low'].min()
        avg_vol     = int(hist['Volume'].mean())
        chg_4w      = (end_price - start_price) / start_price * 100

        # 52주 고저
        hist_1y = tk.history(period='1y')
        high_52 = hist_1y['High'].max() if not hist_1y.empty else None
        low_52  = hist_1y['Low'].min()  if not hist_1y.empty else None

        return {
            'current':  round(end_price, 2),
            'chg_4w':   round(chg_4w, 2),
            'high_4w':  round(high_4w, 2),
            'low_4w':   round(low_4w, 2),
            'high_52':  round(high_52, 2) if high_52 else None,
            'low_52':   round(low_52, 2)  if low_52  else None,
            'avg_vol':  avg_vol,
            'hist':     hist,
            'mkt_cap':  getattr(info, 'market_cap', None),
        }
    except Exception as e:
        print(f'  [주가 오류] {e}')
        return None


def get_analyst_info(symbol):
    """애널리스트 목표주가 및 추천"""
    try:
        tk   = yf.Ticker(symbol)
        info = tk.info
        return {
            'target_mean':   info.get('targetMeanPrice'),
            'target_high':   info.get('targetHighPrice'),
            'target_low':    info.get('targetLowPrice'),
            'recommendation': info.get('recommendationKey', ''),
            'num_analysts':  info.get('numberOfAnalystOpinions', 0),
        }
    except Exception:
        return {}


def get_news(news_query, days=14):
    """Google News RSS 2주치 헤드라인"""
    query = news_query.replace(' ', '+')
    url   = (f'https://news.google.com/rss/search?q={query}'
             f'&hl=en-US&gl=US&ceid=US:en')
    try:
        resp = requests.get(url, timeout=10,
                            headers={'User-Agent': 'Mozilla/5.0'})
        root = ET.fromstring(resp.content)
        headlines = []
        for item in root.findall('.//item')[:15]:
            t = item.find('title')
            if t is not None and t.text:
                headlines.append(t.text.strip())
        return headlines[:10]
    except Exception:
        return []


# ── Groq 분석 ─────────────────────────────────────────────────────────
_RESEARCH_PROMPT = """\
아래는 {name}({symbol})에 대한 최근 2주 데이터다.

[주가 현황]
현재가: ${price} | 4주 등락: {chg:+.1f}% | 4주 고/저: ${high}/${low}
애널리스트 목표주가 평균: {target} | 추천: {rec}

[최근 뉴스 헤드라인]
{headlines}

투자 리서치 리포트를 한국어로 작성해줘. 아래 섹션 순서로:

1. 핵심 요약 (3줄 이내)
2. 강점 / 기회 요인 (3가지, 각 40자 이내)
3. 리스크 / 약점 요인 (3가지, 각 40자 이내)
4. 향후 2주 전망 (3줄 이내, 단정 말고 "~가능성" 어조)
5. 투자 주목 포인트 (AP팀 체크리스트 2가지)

한자 금지. 전문적이되 이해하기 쉽게."""


def generate_analysis(stock, price_data, analyst, headlines):
    target_str = f'${analyst["target_mean"]:.2f}' if analyst.get('target_mean') else '미제공'
    rec_map    = {'buy': '매수', 'hold': '보유', 'sell': '매도',
                  'strong_buy': '강력매수', 'underperform': '매도'}
    rec_str    = rec_map.get(analyst.get('recommendation', ''), analyst.get('recommendation', '미제공'))
    hl_text    = '\n'.join(f'- {h}' for h in headlines[:8])

    prompt = _RESEARCH_PROMPT.format(
        name=stock['name'], symbol=stock['symbol'],
        price=price_data['current'], chg=price_data['chg_4w'],
        high=price_data['high_4w'], low=price_data['low_4w'],
        target=target_str, rec=rec_str,
        headlines=hl_text
    )
    return groq_client.call(prompt, max_tokens=1000, temperature=0.4)


# ── PDF 생성 ──────────────────────────────────────────────────────────
def build_pdf(stock, price_data, analyst, analysis_text, output_path):
    font_name = _register_font()
    doc = SimpleDocTemplate(
        output_path,
        pagesize=A4,
        leftMargin=20*mm, rightMargin=20*mm,
        topMargin=20*mm, bottomMargin=20*mm,
    )

    # 스타일 정의
    base = ParagraphStyle('base', fontName=font_name, fontSize=10, leading=16)
    h1   = ParagraphStyle('h1',   fontName=font_name, fontSize=18, leading=24,
                           textColor=colors.HexColor('#1a1a2e'), spaceAfter=4)
    h2   = ParagraphStyle('h2',   fontName=font_name, fontSize=13, leading=18,
                           textColor=colors.HexColor('#16213e'), spaceBefore=12, spaceAfter=4)
    body = ParagraphStyle('body', fontName=font_name, fontSize=10, leading=16,
                           spaceAfter=4)
    small= ParagraphStyle('small',fontName=font_name, fontSize=8, leading=12,
                           textColor=colors.grey)

    story = []

    # ── 헤더 ──
    today_str = datetime.now().strftime('%Y년 %m월 %d일')
    story.append(Paragraph(f'GM Capital  리서치 리포트', h1))
    story.append(Paragraph(f'{stock["name"]} ({stock["symbol"]})  |  {today_str}', base))
    story.append(HRFlowable(width='100%', thickness=2,
                             color=colors.HexColor('#1a1a2e'), spaceAfter=12))

    # ── 주가 요약 테이블 ──
    chg_color = colors.HexColor('#006400') if price_data['chg_4w'] >= 0 else colors.HexColor('#8b0000')
    sign = '+' if price_data['chg_4w'] >= 0 else ''

    table_data = [
        ['현재가', '4주 등락', '4주 고가', '4주 저가', '52주 고가', '52주 저가'],
        [
            f'${price_data["current"]}',
            f'{sign}{price_data["chg_4w"]}%',
            f'${price_data["high_4w"]}',
            f'${price_data["low_4w"]}',
            f'${price_data["high_52"]}' if price_data['high_52'] else 'N/A',
            f'${price_data["low_52"]}'  if price_data['low_52']  else 'N/A',
        ],
    ]

    t = Table(table_data, colWidths=[28*mm]*6)
    t.setStyle(TableStyle([
        ('BACKGROUND', (0,0), (-1,0), colors.HexColor('#1a1a2e')),
        ('TEXTCOLOR',  (0,0), (-1,0), colors.white),
        ('FONTNAME',   (0,0), (-1,-1), font_name),
        ('FONTSIZE',   (0,0), (-1,-1), 9),
        ('ALIGN',      (0,0), (-1,-1), 'CENTER'),
        ('VALIGN',     (0,0), (-1,-1), 'MIDDLE'),
        ('ROWBACKGROUNDS', (0,1), (-1,-1), [colors.HexColor('#f8f9fa'), colors.white]),
        ('GRID',       (0,0), (-1,-1), 0.5, colors.HexColor('#dee2e6')),
        ('FONTSIZE',   (1,1), (1,1), 12),
    ]))
    story.append(t)
    story.append(Spacer(1, 8*mm))

    # 애널리스트 정보
    if analyst.get('target_mean'):
        rec_map = {'buy': '매수', 'hold': '보유', 'sell': '매도',
                   'strong_buy': '강력매수', 'underperform': '매도'}
        rec_str = rec_map.get(analyst.get('recommendation', ''), analyst.get('recommendation', '-'))
        ana_text = (f'애널리스트 목표주가: 평균 ${analyst["target_mean"]:.2f}'
                    f'  (최고 ${analyst.get("target_high", "-")} / 최저 ${analyst.get("target_low", "-")})'
                    f'  |  추천: {rec_str}  |  커버리지 {analyst.get("num_analysts", 0)}개사')
        story.append(Paragraph(ana_text, small))
        story.append(Spacer(1, 4*mm))

    # ── 분석 본문 ──
    story.append(HRFlowable(width='100%', thickness=0.5,
                             color=colors.HexColor('#dee2e6'), spaceAfter=8))

    for line in analysis_text.splitlines():
        line = line.strip()
        if not line:
            story.append(Spacer(1, 3*mm))
            continue

        # 섹션 제목 감지
        if re.match(r'^\d+\.', line) or line.endswith(':') or '##' in line:
            clean = re.sub(r'^#+\s*', '', line).strip()
            story.append(Paragraph(clean, h2))
        elif line.startswith(('-', '•', '*')):
            clean = line.lstrip('-•* ').strip()
            story.append(Paragraph(f'&nbsp;&nbsp;&nbsp;• {clean}', body))
        else:
            story.append(Paragraph(line, body))

    story.append(Spacer(1, 8*mm))
    story.append(HRFlowable(width='100%', thickness=0.5,
                             color=colors.HexColor('#dee2e6')))
    story.append(Spacer(1, 4*mm))
    story.append(Paragraph(
        '본 리포트는 AI(Groq Llama 3)가 생성한 참고 자료입니다. '
        'AP팀 검수 후 투자 판단에 활용하시기 바랍니다. '
        'GM Capital 내부용.',
        small))

    doc.build(story)
    return output_path


# ── 텔레그램 PDF 전송 ─────────────────────────────────────────────────
def send_telegram_doc(pdf_path, caption):
    token   = _cfg['telegram_token']
    chat_id = _cfg['telegram_chat_id']
    url     = f'https://api.telegram.org/bot{token}/sendDocument'
    with open(pdf_path, 'rb') as f:
        requests.post(url,
            data={'chat_id': chat_id, 'caption': caption, 'parse_mode': 'HTML'},
            files={'document': f},
            timeout=30)


def send_telegram(text):
    token   = _cfg['telegram_token']
    chat_id = _cfg['telegram_chat_id']
    url     = f'https://api.telegram.org/bot{token}/sendMessage'
    requests.post(url,
        json={'chat_id': chat_id, 'text': text, 'parse_mode': 'HTML'},
        timeout=15)


# ── 메인 ────────────────────────────────────────────────────────────
def run_research_report(force_ticker=None):
    if force_ticker:
        stock = next((s for s in ROTATION if s['symbol'] == force_ticker), None)
        if not stock:
            print(f'  티커 없음: {force_ticker}')
            return
        index = ROTATION.index(stock)
    else:
        stock, index = _get_current_stock()

    print(f'[{datetime.now().strftime("%Y-%m-%d %H:%M")}] '
          f'격주 리서치 리포트: {stock["name"]} ({stock["symbol"]})')

    # 데이터 수집
    print('  주가 데이터 수집...')
    price_data = get_price_data(stock['symbol'])
    if not price_data:
        send_telegram(f'리서치 리포트 오류: {stock["name"]} 주가 데이터 없음')
        return

    print('  애널리스트 데이터 수집...')
    analyst = get_analyst_info(stock['symbol'])

    print('  뉴스 수집...')
    headlines = get_news(stock['news_query'], days=14)

    # LLM 분석
    print('  AI 분석 생성...')
    analysis = generate_analysis(stock, price_data, analyst, headlines)

    # PDF 생성
    today_str  = datetime.now().strftime('%Y%m%d')
    pdf_name   = f'research_{stock["symbol"]}_{today_str}.pdf'
    pdf_path   = os.path.join(BASE_DIR, pdf_name)
    print('  PDF 생성...')
    build_pdf(stock, price_data, analyst, analysis, pdf_path)

    # 텔레그램 전송
    caption = (f'<b>📄 격주 리서치 리포트</b>\n'
               f'{stock["name"]} ({stock["symbol"]})\n'
               f'{datetime.now().strftime("%Y.%m.%d")}\n'
               f'<i>AP팀 검수 후 활용 권장</i>')
    print('  텔레그램 전송...')
    send_telegram_doc(pdf_path, caption)

    # 상태 저장 (다음 실행 시 다음 종목)
    if not force_ticker:
        _save_state(index)

    # PDF 파일 정리
    try:
        os.remove(pdf_path)
    except Exception:
        pass

    print('리서치 리포트 완료')


if __name__ == '__main__':
    import sys
    force = sys.argv[1] if len(sys.argv) > 1 else None
    run_research_report(force_ticker=force)
