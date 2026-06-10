"""
GM Capital 투자일지 자동화
OneDrive 엑셀 감시 → 미분석 행 탐지 → Groq 분석 → 자동 기입
+ yfinance 현재가 1시간 자동 업데이트
"""
import json, os, time, threading, traceback
from datetime import datetime, date

import openpyxl
from openpyxl.styles import PatternFill, Font, Alignment, Border, Side
from openpyxl.utils import get_column_letter
from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler
import yfinance as yf
import requests

import groq_client

# ── 경로 설정 ────────────────────────────────────────────────────────
BASE_DIR   = os.path.dirname(os.path.abspath(__file__))
ONEDRIVE   = r'C:\Users\DeskTop\OneDrive\문서'
EXCEL_PATH = os.path.join(ONEDRIVE, 'GMCapital_투자일지.xlsx')

with open(os.path.join(BASE_DIR, 'config.json'), 'r', encoding='utf-8') as f:
    _cfg = json.load(f)

# ── 컬러 팔레트 ─────────────────────────────────────────────────────
C_BG     = '0D1B2A'
C_HEADER = '1A2F4A'
C_GOLD   = 'C9A84C'
C_GREEN  = '00C896'
C_RED    = 'FF4D4D'
C_INPUT  = '1E3A5F'
C_AUTO   = '162035'
C_AI     = '0A1F2E'
C_FB     = '1A1030'
C_WHITE  = 'FFFFFF'
C_YELLOW = 'FFE066'

def _fill(c): return PatternFill('solid', fgColor=c)
def _font(bold=False, color=C_WHITE, size=10):
    return Font(bold=bold, color=color, size=size, name='맑은 고딕')
def _align(h='center', v='center', wrap=True):
    return Alignment(horizontal=h, vertical=v, wrap_text=wrap)
def _border():
    s = Side(style='thin', color='2A4A6A')
    return Border(left=s, right=s, top=s, bottom=s)


# ── 컬럼 정의 ────────────────────────────────────────────────────────
# 미래에셋 (국내주식, 원화)
# A~H: 입력 | I~K: 자동계산 | L~P: AI분석 | Q~R: 피드백
DOM_COLS = [
    ('A','날짜',12),       ('B','구분',8),         ('C','티커',10),
    ('D','종목명',18),     ('E','매수가(₩)',13),    ('F','수량',7),
    ('G','매도가(₩)',13),  ('H','메모',18),
    ('I','투자금액',13),   ('J','수익금액',13),     ('K','수익률(%)',11),
    ('L','매수 핵심 논거',50),
    ('M','리스크 요인',30),
    ('N','목표가(₩)',13),  ('O','손절가(₩)',13),   ('P','종합판단',12),
    ('Q','내 의견',22),    ('R','분석완료',9),
]
DOM_AI_COLS   = range(12, 17)   # L~P (1-indexed)
DOM_FB_COLS   = range(17, 19)   # Q~R
DOM_DONE_COL  = 18              # R열

# 메리츠 (미국주식, 달러)
# A~H: 입력 | I~M: 자동계산 | N~R: AI분석 | S~T: 피드백
US_COLS = [
    ('A','날짜',12),       ('B','구분',8),          ('C','티커',10),
    ('D','종목명',22),     ('E','매수가($)',13),     ('F','수량',7),
    ('G','매도가($)',13),  ('H','메모',18),
    ('I','매수시환율',13), ('J','투자금액($)',14),   ('K','투자금액(₩)',15),
    ('L','수익금액($)',14),('M','수익률(%)',11),
    ('N','매수 핵심 논거',50),
    ('O','리스크 요인',30),
    ('P','목표가($)',13),  ('Q','손절가($)',13),     ('R','종합판단',12),
    ('S','내 의견',22),    ('T','분석완료',9),
]
US_AI_COLS   = range(14, 19)    # N~R
US_FB_COLS   = range(19, 21)    # S~T
US_DONE_COL  = 20               # T열


# ── 엑셀 파일 생성 ───────────────────────────────────────────────────
def create_excel():
    wb = openpyxl.Workbook()
    wb.remove(wb.active)
    _build_sheet(wb, '미래에셋_매매일지', DOM_COLS, DOM_AI_COLS, DOM_FB_COLS, 'C9A84C', _dom_formulas)
    _build_sheet(wb, '메리츠_매매일지',   US_COLS,  US_AI_COLS,  US_FB_COLS,  '4A90D9', _us_formulas)
    _build_dashboard(wb)
    _build_history(wb)
    wb.save(EXCEL_PATH)
    print(f'  엑셀 생성 완료: {EXCEL_PATH}')


def _build_sheet(wb, name, cols, ai_range, fb_range, tab_color, formula_fn):
    ws = wb.create_sheet(name)
    ws.sheet_view.showGridLines = False
    ws.freeze_panes = 'A2'
    ws.sheet_properties.tabColor = tab_color

    # 헤더
    for ci, (_, col_name, width) in enumerate(cols, 1):
        c = ws.cell(1, ci, col_name)
        c.fill = _fill(C_HEADER)
        c.font = _font(bold=True, color=C_GOLD)
        c.alignment = _align()
        c.border = _border()
        ws.column_dimensions[get_column_letter(ci)].width = width
    ws.row_dimensions[1].height = 22

    # 데이터 행
    for row in range(2, 201):
        n_cols = len(cols)
        for ci in range(1, n_cols + 1):
            c = ws.cell(row, ci)
            if ci <= 8:
                c.fill = _fill(C_INPUT)
            elif ci <= 8 + 5:    # 자동계산 (최대 5열)
                in_auto = ci not in list(ai_range) and ci not in list(fb_range)
                c.fill = _fill(C_AUTO) if in_auto else _fill(C_AI)
            if ci in ai_range:
                c.fill = _fill(C_AI)
            elif ci in fb_range:
                c.fill = _fill(C_FB)
            c.font = _font(color=C_WHITE)
            c.alignment = _align()  # 모두 가운데 정렬, wrap
            c.border = _border()
        formula_fn(ws, row)
    ws.row_dimensions[1].height = 22


def _dom_formulas(ws, row):
    ws.cell(row, 9).value  = f'=IF(E{row}<>"",E{row}*F{row},"")'
    ws.cell(row, 10).value = f'=IF(AND(E{row}<>"",G{row}<>""),(G{row}-E{row})*F{row},"")'
    ws.cell(row, 11).value = f'=IF(AND(E{row}<>"",G{row}<>""),ROUND((G{row}-E{row})/E{row}*100,2),"")'


def _us_formulas(ws, row):
    ws.cell(row, 10).value = f'=IF(E{row}<>"",E{row}*F{row},"")'
    ws.cell(row, 11).value = f'=IF(AND(E{row}<>"",I{row}<>""),J{row}*I{row},"")'
    ws.cell(row, 12).value = f'=IF(AND(E{row}<>"",G{row}<>""),(G{row}-E{row})*F{row},"")'
    ws.cell(row, 13).value = f'=IF(AND(E{row}<>"",G{row}<>""),ROUND((G{row}-E{row})/E{row}*100,2),"")'


def _build_dashboard(wb):
    ws = wb.create_sheet('대시보드')
    ws.sheet_view.showGridLines = False
    ws.sheet_properties.tabColor = '00C896'
    for row in range(1, 50):
        for col in range(1, 20):
            ws.cell(row, col).fill = _fill(C_BG)
    ws.merge_cells('A1:R1')
    t = ws['A1']
    t.value     = 'GM Capital  투자 대시보드'
    t.fill      = _fill(C_HEADER)
    t.font      = Font(bold=True, color=C_GOLD, size=16, name='맑은 고딕')
    t.alignment = _align()
    ws.row_dimensions[1].height = 36


def _build_history(wb):
    ws = wb.create_sheet('분석히스토리')
    ws.sheet_view.showGridLines = False
    ws.sheet_properties.tabColor = '888888'
    cols = [('A','분석일시',18),('B','계좌',12),('C','티커',10),('D','구분',12),('E','분석내용',90)]
    for ci, (_, n, w) in enumerate(cols, 1):
        c = ws.cell(1, ci, n)
        c.fill = _fill(C_HEADER); c.font = _font(bold=True, color=C_GOLD)
        c.alignment = _align(); c.border = _border()
        ws.column_dimensions[get_column_letter(ci)].width = w
    for row in range(2, 201):
        for ci in range(1, 6):
            c = ws.cell(row, ci)
            c.fill = _fill(C_BG); c.font = _font(color=C_WHITE, size=9)
            c.alignment = _align(); c.border = _border()


# ── 뉴스 수집 ────────────────────────────────────────────────────────
def _get_news(ticker):
    try:
        import xml.etree.ElementTree as ET
        url = f'https://news.google.com/rss/search?q={ticker}+stock+analysis&hl=en-US&gl=US&ceid=US:en'
        resp = requests.get(url, timeout=8, headers={'User-Agent': 'Mozilla/5.0'})
        root = ET.fromstring(resp.content)
        titles = [item.find('title').text.strip()
                  for item in root.findall('.//item')[:5]
                  if item.find('title') is not None]
        return ' / '.join(titles[:3]) if titles else '뉴스 없음'
    except Exception:
        return '뉴스 수집 실패'


def _get_usd_krw():
    try:
        hist = yf.Ticker('USDKRW=X').history(period='2d')
        return round(float(hist['Close'].iloc[-1]), 0) if not hist.empty else 1380.0
    except Exception:
        return 1380.0


def _get_price_info(ticker):
    """yfinance로 현재가·52주 고저·PE·시총 등 기초 데이터"""
    try:
        tk   = yf.Ticker(ticker)
        info = tk.info
        hist = tk.history(period='1y')
        return {
            'current':   round(info.get('currentPrice') or info.get('regularMarketPrice', 0), 2),
            'high_52w':  round(float(hist['High'].max()), 2) if not hist.empty else 0,
            'low_52w':   round(float(hist['Low'].min()), 2)  if not hist.empty else 0,
            'pe':        round(info.get('trailingPE', 0) or 0, 1),
            'target':    round(info.get('targetMeanPrice', 0) or 0, 2),
            'rec':       info.get('recommendationKey', ''),
            'sector':    info.get('sector', ''),
            'mkt_cap':   info.get('marketCap', 0),
        }
    except Exception:
        return {}


# ── 분석 프롬프트 (상세 논거 중심) ──────────────────────────────────
_DOM_PROMPT = """\
국내 ETF/주식 투자 분석을 해줘. 투자자에게 실질적으로 도움이 되는 깊은 분석이 목적이야.

종목: {name} (코드 {ticker})
매수 평균단가: {price:,}원 | 수량: {qty}주 | 총 투자금: {amount:,}원
메모: {memo}
최근 뉴스: {news}

아래 JSON 형식으로만 응답해. 다른 텍스트 없이:
{{
  "핵심논거": "이 종목을 지금 이 가격에 보유할 이유를 구체적으로. 어떤 매크로 환경·섹터 트렌드·특수 요인이 작동하는지, 단순 '상승 예상'이 아닌 실질 근거 3가지 이상. 200자 이내",
  "리스크": "가장 현실적인 하방 리스크 2가지, 각 항목 앞에 번호. 100자 이내",
  "목표가": 숫자만(원),
  "손절가": 숫자만(원),
  "종합판단": "매수적절 or 주의 or 부적절"
}}
한자 금지. "~할 것 같다" 금지. 분석가 어조로."""

_US_PROMPT = """\
미국 주식/ETF 투자 분석을 해줘. 투자자에게 실질적으로 도움이 되는 깊은 분석이 목적이야.

종목: {name} ({ticker})
매수 평균단가: ${price} | 수량: {qty}주 | 매수 당시 환율: {rate}원
현재가: ${current} | 52주 고가: ${high52} | 52주 저가: ${low52}
애널리스트 목표주가: ${target} | 추천: {rec} | 섹터: {sector}
메모: {memo}
최근 뉴스: {news}

아래 JSON 형식으로만 응답해. 다른 텍스트 없이:
{{
  "핵심논거": "이 종목을 지금 이 가격에 보유할 이유를 구체적으로. 어떤 매크로 환경·섹터 트렌드·기업 펀더멘털이 작동하는지, 현재가 기준 밸류에이션 평가 포함, 단순 '좋아 보인다'가 아닌 실질 근거 3가지 이상. 250자 이내",
  "리스크": "가장 현실적인 하방 리스크 2가지, 각 항목 앞에 번호. 120자 이내",
  "목표가": 숫자만(달러),
  "손절가": 숫자만(달러),
  "종합판단": "매수적절 or 주의 or 부적절"
}}
한자 금지. "~할 것 같다" 금지. 분석가 어조로."""


def analyze_domestic(ticker, name, price, qty, memo):
    news   = _get_news(str(ticker))
    amount = price * qty
    prompt = _DOM_PROMPT.format(
        name=name, ticker=ticker, price=int(price),
        qty=qty, amount=int(amount), memo=memo or '없음', news=news)
    try:
        import re
        raw  = groq_client.call(prompt, max_tokens=600, temperature=0.4)
        m    = re.search(r'\{.*\}', raw, re.DOTALL)
        return json.loads(m.group()) if m else {}
    except Exception as e:
        print(f'  [분석 오류] {e}')
        return {}


def analyze_us(ticker, name, price, qty, memo, rate):
    news  = _get_news(ticker)
    pinfo = _get_price_info(ticker)
    prompt = _US_PROMPT.format(
        name=name, ticker=ticker, price=price, qty=qty, rate=int(rate),
        current=pinfo.get('current', price),
        high52=pinfo.get('high_52w', 0), low52=pinfo.get('low_52w', 0),
        target=pinfo.get('target', 0), rec=pinfo.get('rec', '미제공'),
        sector=pinfo.get('sector', '미제공'),
        memo=memo or '없음', news=news)
    try:
        import re
        raw  = groq_client.call(prompt, max_tokens=700, temperature=0.4)
        m    = re.search(r'\{.*\}', raw, re.DOTALL)
        return json.loads(m.group()) if m else {}
    except Exception as e:
        print(f'  [분석 오류] {e}')
        return {}


# ── 엑셀 기입 ────────────────────────────────────────────────────────
def _judgment_color(val):
    if val == '매수적절': return C_GREEN
    if val == '부적절':   return C_RED
    return C_YELLOW

def _write_domestic(ws, row, result):
    mapping = {
        12: result.get('핵심논거',''),   # L
        13: result.get('리스크',''),     # M
        14: result.get('목표가',''),     # N
        15: result.get('손절가',''),     # O
        16: result.get('종합판단',''),   # P
    }
    for col, val in mapping.items():
        c = ws.cell(row, col, val)
        c.alignment = _align()
        if col == 16:
            c.font = _font(bold=True, color=_judgment_color(str(val)))
    ws.cell(row, DOM_DONE_COL).value = 'Y'
    ws.row_dimensions[row].height = 60


def _write_us(ws, row, result, rate):
    ws.cell(row, 9).value = rate   # 환율
    mapping = {
        14: result.get('핵심논거',''),   # N
        15: result.get('리스크',''),     # O
        16: result.get('목표가',''),     # P
        17: result.get('손절가',''),     # Q
        18: result.get('종합판단',''),   # R
    }
    for col, val in mapping.items():
        c = ws.cell(row, col, val)
        c.alignment = _align()
        if col == 18:
            c.font = _font(bold=True, color=_judgment_color(str(val)))
    ws.cell(row, US_DONE_COL).value = 'Y'
    ws.row_dimensions[row].height = 60


def _write_history(wb, account, ticker, kind, content):
    ws       = wb['분석히스토리']
    next_row = max(2, ws.max_row + 1)
    if ws.cell(2, 1).value is None:
        next_row = 2
    data = [datetime.now().strftime('%Y-%m-%d %H:%M'), account, ticker, kind, content]
    for ci, val in enumerate(data, 1):
        c = ws.cell(next_row, ci, val)
        c.fill = _fill(C_BG); c.font = _font(color=C_WHITE, size=9)
        c.alignment = _align(); c.border = _border()


# ── 미분석 행 처리 ───────────────────────────────────────────────────
_lock = threading.Lock()

def process_excel():
    if not os.path.exists(EXCEL_PATH):
        create_excel()
        return

    with _lock:
        try:
            wb      = openpyxl.load_workbook(EXCEL_PATH)
            rate    = _get_usd_krw()
            changed = False

            # 미래에셋
            ws_dom = wb['미래에셋_매매일지']
            for row in range(2, ws_dom.max_row + 2):
                ticker = ws_dom.cell(row, 3).value
                price  = ws_dom.cell(row, 5).value
                done   = ws_dom.cell(row, DOM_DONE_COL).value
                if not ticker or not price or done == 'Y':
                    continue
                name   = ws_dom.cell(row, 4).value or str(ticker)
                qty    = ws_dom.cell(row, 6).value or 0
                memo   = ws_dom.cell(row, 8).value or ''
                print(f'  [분석] 미래에셋 {name} (행 {row})')
                result = analyze_domestic(ticker, name, price, qty, memo)
                if result:
                    _write_domestic(ws_dom, row, result)
                    _write_history(wb, '미래에셋', str(ticker), '매수분석',
                                   result.get('핵심논거',''))
                    changed = True

            # 메리츠
            ws_us = wb['메리츠_매매일지']
            for row in range(2, ws_us.max_row + 2):
                ticker = ws_us.cell(row, 3).value
                price  = ws_us.cell(row, 5).value
                done   = ws_us.cell(row, US_DONE_COL).value
                if not ticker or not price or done == 'Y':
                    continue
                name = ws_us.cell(row, 4).value or str(ticker)
                qty  = ws_us.cell(row, 6).value or 0
                memo = ws_us.cell(row, 8).value or ''
                print(f'  [분석] 메리츠 {ticker} (행 {row})')
                result = analyze_us(ticker, name, price, qty, memo, rate)
                if result:
                    _write_us(ws_us, row, result, rate)
                    _write_history(wb, '메리츠', ticker, '매수분석',
                                   result.get('핵심논거',''))
                    changed = True

            if changed:
                wb.save(EXCEL_PATH)
                print(f'  저장 완료 ({datetime.now().strftime("%H:%M:%S")})')
            else:
                print('  미분석 행 없음')

        except Exception as e:
            print(f'  [처리 오류] {e}')
            traceback.print_exc()


# ── 현재가 업데이트 ──────────────────────────────────────────────────
def update_prices():
    if not os.path.exists(EXCEL_PATH):
        return
    try:
        wb   = openpyxl.load_workbook(EXCEL_PATH)
        rate = _get_usd_krw()
        ws   = wb['메리츠_매매일지']
        rows = [(r, ws.cell(r,3).value) for r in range(2, ws.max_row+1)
                if ws.cell(r,3).value and not ws.cell(r,7).value]
        if rows:
            syms = list({t for _,t in rows})
            data = yf.download(syms, period='2d', progress=False, auto_adjust=True)
            changed = False
            for row, ticker in rows:
                try:
                    price = (float(data['Close'][ticker].iloc[-1])
                             if len(syms) > 1 else float(data['Close'].iloc[-1]))
                    ws.cell(row, 9).value = rate
                    changed = True
                except Exception:
                    continue
            if changed:
                wb.save(EXCEL_PATH)
                print(f'  환율 업데이트 ({rate:.0f}원)')
    except Exception as e:
        print(f'  [현재가 오류] {e}')


# ── Watchdog ────────────────────────────────────────────────────────
class ExcelHandler(FileSystemEventHandler):
    def __init__(self):
        self._last = 0

    def on_modified(self, event):
        if 'GMCapital_투자일지' in event.src_path:
            now = time.time()
            if now - self._last < 5:
                return
            self._last = now
            print(f'  [감지] 파일 변경 → 분석 시작')
            threading.Thread(target=process_excel, daemon=True).start()


def _price_loop():
    while True:
        time.sleep(3600)
        update_prices()


# ── 메인 ────────────────────────────────────────────────────────────
def main():
    if not os.path.exists(EXCEL_PATH):
        print('엑셀 파일 생성 중...')
        create_excel()

    print('미분석 행 초기 점검...')
    process_excel()

    threading.Thread(target=_price_loop, daemon=True).start()

    observer = Observer()
    observer.schedule(ExcelHandler(), path=os.path.dirname(EXCEL_PATH), recursive=False)
    observer.start()
    print(f'OneDrive 감시 시작. 종료: Ctrl+C')

    try:
        while True:
            time.sleep(10)
    except KeyboardInterrupt:
        observer.stop()
    observer.join()


if __name__ == '__main__':
    main()
