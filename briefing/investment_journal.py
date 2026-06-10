"""
GM Capital 투자일지 자동화
OneDrive 엑셀 감시 → 미분석 행 탐지 → Groq 분석 → 자동 기입
+ yfinance 현재가 1시간 자동 업데이트
"""
import json, os, time, threading, traceback
from datetime import datetime, date

import openpyxl
from openpyxl.styles import (PatternFill, Font, Alignment, Border, Side,
                              GradientFill)
from openpyxl.utils import get_column_letter
from openpyxl.chart import BarChart, PieChart, LineChart, Reference
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
C_BG      = '0D1B2A'   # 다크 네이비
C_HEADER  = '1A2F4A'   # 진한 네이비
C_GOLD    = 'C9A84C'   # 골드
C_GREEN   = '00C896'   # 수익 초록
C_RED     = 'FF4D4D'   # 손실 빨강
C_INPUT   = '1E3A5F'   # 입력 영역
C_AUTO    = '162035'   # 자동계산 영역
C_AI      = '0F2A1A'   # AI 분석 영역
C_FB      = '2A1A2E'   # 피드백 영역
C_WHITE   = 'FFFFFF'
C_YELLOW  = 'FFE066'

def _fill(hex_color):
    return PatternFill('solid', fgColor=hex_color)

def _font(bold=False, color=C_WHITE, size=10):
    return Font(bold=bold, color=color, size=size, name='맑은 고딕')

def _align(h='center', v='center', wrap=True):
    return Alignment(horizontal=h, vertical=v, wrap_text=wrap)

def _border():
    s = Side(style='thin', color='2A4A6A')
    return Border(left=s, right=s, top=s, bottom=s)


# ── 엑셀 파일 생성 ───────────────────────────────────────────────────
def create_excel():
    """GMCapital_투자일지.xlsx 초기 생성"""
    wb = openpyxl.Workbook()
    wb.remove(wb.active)

    _build_domestic(wb)
    _build_us(wb)
    _build_dashboard(wb)
    _build_history(wb)

    wb.save(EXCEL_PATH)
    print(f'  엑셀 생성 완료: {EXCEL_PATH}')


def _header_row(ws, cols, row=1):
    """헤더 행 스타일 적용"""
    for col_idx, (col_letter, name, width) in enumerate(cols, 1):
        cell = ws.cell(row=row, column=col_idx, value=name)
        cell.fill      = _fill(C_HEADER)
        cell.font      = _font(bold=True, color=C_GOLD)
        cell.alignment = _align()
        cell.border    = _border()
        ws.column_dimensions[get_column_letter(col_idx)].width = width
    ws.row_dimensions[row].height = 22


def _build_domestic(wb):
    ws = wb.create_sheet('미래에셋_매매일지')
    ws.sheet_view.showGridLines = False
    ws.freeze_panes = 'A2'

    cols = [
        # (letter, name, width)
        ('A','날짜',12), ('B','구분',10), ('C','티커',10), ('D','종목명',16),
        ('E','매수가(₩)',13), ('F','수량',8), ('G','매도가(₩)',13), ('H','메모',20),
        ('I','투자금액(₩)',15), ('J','수익금액(₩)',15), ('K','수익률(%)',12),
        ('L','매수근거',30), ('M','리스크요인',28), ('N','목표가(₩)',13),
        ('O','1차 매도시점',18), ('P','2차 매도시점',18), ('Q','손절가(₩)',13),
        ('R','종합판단',14),
        ('S','내 의견',22), ('T','분석완료',10),
    ]
    _header_row(ws, cols)

    # 영역별 배경색 구분 (2~200행 미리 적용)
    for row in range(2, 201):
        bg = C_BG
        for col in range(1, 21):
            cell = ws.cell(row=row, column=col)
            if col <= 8:    cell.fill = _fill(C_INPUT)
            elif col <= 11: cell.fill = _fill(C_AUTO)
            elif col <= 18: cell.fill = _fill(C_AI)
            else:           cell.fill = _fill(C_FB)
            cell.font      = _font(color=C_WHITE)
            cell.alignment = _align(h='left' if col in [4,8,12,13,15,16,19] else 'center')
            cell.border    = _border()

        # 수식 (I, J, K열)
        ws.cell(row=row, column=9).value  = f'=IF(E{row}<>"",E{row}*F{row},"")'
        ws.cell(row=row, column=10).value = f'=IF(AND(E{row}<>"",G{row}<>""),(G{row}-E{row})*F{row},"")'
        ws.cell(row=row, column=11).value = f'=IF(AND(E{row}<>"",G{row}<>""),ROUND((G{row}-E{row})/E{row}*100,2),"")'

    # 수익률 조건부서식은 수동 적용 (openpyxl DifferentialStyle)
    ws.sheet_properties.tabColor = 'C9A84C'


def _build_us(wb):
    ws = wb.create_sheet('메리츠_매매일지')
    ws.sheet_view.showGridLines = False
    ws.freeze_panes = 'A2'

    cols = [
        ('A','날짜',12), ('B','구분',10), ('C','티커',10), ('D','종목명',16),
        ('E','매수가($)',13), ('F','수량',8), ('G','매도가($)',13), ('H','메모',20),
        ('I','매수시환율',13), ('J','투자금액($)',14), ('K','투자금액(₩)',15),
        ('L','수익금액($)',14), ('M','수익률(%)',12),
        ('N','매수근거',30), ('O','리스크요인',28), ('P','목표가($)',13),
        ('Q','1차 매도시점',18), ('R','2차 매도시점',18), ('S','손절가($)',13),
        ('T','종합판단',14),
        ('U','내 의견',22), ('V','분석완료',10),
    ]
    _header_row(ws, cols)

    for row in range(2, 201):
        for col in range(1, 23):
            cell = ws.cell(row=row, column=col)
            if col <= 8:    cell.fill = _fill(C_INPUT)
            elif col <= 13: cell.fill = _fill(C_AUTO)
            elif col <= 20: cell.fill = _fill(C_AI)
            else:           cell.fill = _fill(C_FB)
            cell.font      = _font(color=C_WHITE)
            cell.alignment = _align(h='left' if col in [4,8,14,15,17,18,21] else 'center')
            cell.border    = _border()

        ws.cell(row=row, column=10).value = f'=IF(E{row}<>"",E{row}*F{row},"")'
        ws.cell(row=row, column=11).value = f'=IF(AND(E{row}<>"",I{row}<>""),J{row}*I{row},"")'
        ws.cell(row=row, column=12).value = f'=IF(AND(E{row}<>"",G{row}<>""),(G{row}-E{row})*F{row},"")'
        ws.cell(row=row, column=13).value = f'=IF(AND(E{row}<>"",G{row}<>""),ROUND((G{row}-E{row})/E{row}*100,2),"")'

    ws.sheet_properties.tabColor = '4A90D9'


def _build_dashboard(wb):
    ws = wb.create_sheet('대시보드')
    ws.sheet_view.showGridLines = False

    # 배경 전체 다크
    for row in range(1, 50):
        for col in range(1, 20):
            cell = ws.cell(row=row, column=col)
            cell.fill = _fill(C_BG)

    # 타이틀
    ws.merge_cells('A1:R1')
    t = ws['A1']
    t.value     = 'GM Capital  투자 대시보드'
    t.fill      = _fill(C_HEADER)
    t.font      = Font(bold=True, color=C_GOLD, size=16, name='맑은 고딕')
    t.alignment = _align()
    ws.row_dimensions[1].height = 36

    # 요약 카드 레이블
    cards = [
        ('B3','총 투자금액'), ('E3','총 평가손익'), ('H3','전체 수익률'),
        ('K3','보유 종목'), ('N3','완료 거래'),
    ]
    for addr, label in cards:
        c = ws[addr]
        c.value     = label
        c.fill      = _fill(C_HEADER)
        c.font      = _font(bold=True, color=C_GOLD, size=10)
        c.alignment = _align()

    ws.sheet_properties.tabColor = '00C896'


def _build_history(wb):
    ws = wb.create_sheet('분석히스토리')
    ws.sheet_view.showGridLines = False

    cols = [
        ('A','분석일시',18), ('B','계좌',12), ('C','티커',10),
        ('D','구분',12), ('E','분석내용',80),
    ]
    _header_row(ws, cols)

    for row in range(2, 201):
        for col in range(1, 6):
            cell = ws.cell(row=row, column=col)
            cell.fill      = _fill(C_BG)
            cell.font      = _font(color=C_WHITE, size=9)
            cell.alignment = _align(h='left' if col == 5 else 'center')
            cell.border    = _border()

    ws.sheet_properties.tabColor = '888888'


# ── Groq 분석 프롬프트 ───────────────────────────────────────────────
_DOM_PROMPT = """\
국내 주식 매수 분석을 해줘.

종목: {name}({ticker})
매수가: {price:,}원 | 수량: {qty}주 | 투자금: {amount:,}원
메모: {memo}
최근 뉴스: {news}

아래 JSON 형식으로만 응답:
{{
  "매수근거": "30자 이내",
  "리스크요인": "30자 이내",
  "목표가": 숫자만,
  "1차매도": "조건 20자 이내",
  "2차매도": "조건 20자 이내",
  "손절가": 숫자만,
  "종합판단": "매수적절 or 주의 or 부적절"
}}
한자 금지. 단정 말고 분석 어조."""

_US_PROMPT = """\
미국 주식 매수 분석을 해줘.

종목: {name}({ticker})
매수가: ${price} | 수량: {qty}주 | 환율: {rate}원
메모: {memo}
최근 뉴스: {news}

아래 JSON 형식으로만 응답:
{{
  "매수근거": "30자 이내",
  "리스크요인": "30자 이내",
  "목표가": 숫자만(달러),
  "1차매도": "조건 20자 이내",
  "2차매도": "조건 20자 이내",
  "손절가": 숫자만(달러),
  "종합판단": "매수적절 or 주의 or 부적절"
}}
한자 금지. 단정 말고 분석 어조."""


def _get_news(ticker):
    """Google News RSS로 종목 최근 뉴스 2건"""
    try:
        import xml.etree.ElementTree as ET
        url = (f'https://news.google.com/rss/search?q={ticker}+stock'
               f'&hl=en-US&gl=US&ceid=US:en')
        resp = requests.get(url, timeout=8, headers={'User-Agent': 'Mozilla/5.0'})
        root = ET.fromstring(resp.content)
        titles = []
        for item in root.findall('.//item')[:3]:
            t = item.find('title')
            if t is not None and t.text:
                titles.append(t.text.strip())
        return ' / '.join(titles[:2]) if titles else '뉴스 없음'
    except Exception:
        return '뉴스 수집 실패'


def _get_usd_krw():
    """현재 달러/원 환율"""
    try:
        tk   = yf.Ticker('USDKRW=X')
        hist = tk.history(period='2d')
        return round(float(hist['Close'].iloc[-1]), 0) if not hist.empty else 1350.0
    except Exception:
        return 1350.0


def analyze_domestic(ticker, name, price, qty, memo):
    """국내 주식 매수 분석 → dict"""
    news   = _get_news(ticker)
    amount = price * qty
    prompt = _DOM_PROMPT.format(
        name=name, ticker=ticker, price=int(price),
        qty=qty, amount=int(amount), memo=memo or '없음', news=news)
    try:
        import re
        raw  = groq_client.call(prompt, max_tokens=400)
        m    = re.search(r'\{.*\}', raw, re.DOTALL)
        return json.loads(m.group()) if m else {}
    except Exception as e:
        print(f'  [분석 오류] {e}')
        return {}


def analyze_us(ticker, name, price, qty, memo, rate):
    """미국 주식 매수 분석 → dict"""
    news   = _get_news(ticker)
    prompt = _US_PROMPT.format(
        name=name, ticker=ticker, price=price,
        qty=qty, rate=int(rate), memo=memo or '없음', news=news)
    try:
        import re
        raw  = groq_client.call(prompt, max_tokens=400)
        m    = re.search(r'\{.*\}', raw, re.DOTALL)
        return json.loads(m.group()) if m else {}
    except Exception as e:
        print(f'  [분석 오류] {e}')
        return {}


# ── 엑셀 기입 ────────────────────────────────────────────────────────
def _write_domestic(ws, row, analysis, rate=None):
    ai_cols = {
        'L': analysis.get('매수근거',''),
        'M': analysis.get('리스크요인',''),
        'N': analysis.get('목표가',''),
        'O': analysis.get('1차매도',''),
        'P': analysis.get('2차매도',''),
        'Q': analysis.get('손절가',''),
        'R': analysis.get('종합판단',''),
    }
    for col, val in ai_cols.items():
        cell       = ws[f'{col}{row}']
        cell.value = val
        if col == 'R':
            color = C_GREEN if val == '매수적절' else (C_RED if val == '부적절' else C_YELLOW)
            cell.font = _font(bold=True, color=color)
    ws[f'T{row}'].value = 'Y'


def _write_us(ws, row, analysis, rate):
    ws[f'I{row}'].value = rate
    ai_cols = {
        'N': analysis.get('매수근거',''),
        'O': analysis.get('리스크요인',''),
        'P': analysis.get('목표가',''),
        'Q': analysis.get('1차매도',''),
        'R': analysis.get('2차매도',''),
        'S': analysis.get('손절가',''),
        'T': analysis.get('종합판단',''),
    }
    for col, val in ai_cols.items():
        cell       = ws[f'{col}{row}']
        cell.value = val
        if col == 'T':
            color = C_GREEN if val == '매수적절' else (C_RED if val == '부적절' else C_YELLOW)
            cell.font = _font(bold=True, color=color)
    ws[f'V{row}'].value = 'Y'


def _write_history(wb, account, ticker, kind, content):
    ws = wb['분석히스토리']
    next_row = ws.max_row + 1
    if next_row == 2 and ws.cell(2,1).value is None:
        next_row = 2
    data = [datetime.now().strftime('%Y-%m-%d %H:%M'), account, ticker, kind, content]
    for col, val in enumerate(data, 1):
        cell       = ws.cell(row=next_row, column=col, value=val)
        cell.fill  = _fill(C_BG)
        cell.font  = _font(color=C_WHITE, size=9)
        cell.alignment = _align(h='left' if col == 5 else 'center')
        cell.border    = _border()


# ── 미분석 행 처리 ───────────────────────────────────────────────────
_lock = threading.Lock()

def process_excel():
    """미분석 행 탐지 → 분석 → 기입"""
    if not os.path.exists(EXCEL_PATH):
        print('  엑셀 파일 없음 — 생성합니다')
        create_excel()
        return

    with _lock:
        try:
            wb   = openpyxl.load_workbook(EXCEL_PATH)
            rate = _get_usd_krw()
            changed = False

            # 미래에셋 (T열 = 20번째)
            ws_dom = wb['미래에셋_매매일지']
            for row in range(2, ws_dom.max_row + 1):
                ticker = ws_dom.cell(row, 3).value
                price  = ws_dom.cell(row, 5).value
                done   = ws_dom.cell(row, 20).value  # T열
                if not ticker or not price or done == 'Y':
                    continue

                name  = ws_dom.cell(row, 4).value or ticker
                qty   = ws_dom.cell(row, 6).value or 0
                memo  = ws_dom.cell(row, 8).value or ''
                print(f'  [분석] 미래에셋 {ticker} (행 {row})')
                result = analyze_domestic(ticker, name, price, qty, memo)
                if result:
                    _write_domestic(ws_dom, row, result)
                    _write_history(wb, '미래에셋', ticker, '매수분석', json.dumps(result, ensure_ascii=False))
                    changed = True

            # 메리츠 (V열 = 22번째)
            ws_us = wb['메리츠_매매일지']
            for row in range(2, ws_us.max_row + 1):
                ticker = ws_us.cell(row, 3).value
                price  = ws_us.cell(row, 5).value
                done   = ws_us.cell(row, 22).value  # V열
                if not ticker or not price or done == 'Y':
                    continue

                name = ws_us.cell(row, 4).value or ticker
                qty  = ws_us.cell(row, 6).value or 0
                memo = ws_us.cell(row, 8).value or ''
                print(f'  [분석] 메리츠 {ticker} (행 {row})')
                result = analyze_us(ticker, name, price, qty, memo, rate)
                if result:
                    _write_us(ws_us, row, result, rate)
                    _write_history(wb, '메리츠', ticker, '매수분석', json.dumps(result, ensure_ascii=False))
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
    """보유 종목 현재가 자동 업데이트 (1시간 주기)"""
    if not os.path.exists(EXCEL_PATH):
        return
    try:
        wb      = openpyxl.load_workbook(EXCEL_PATH)
        rate    = _get_usd_krw()
        changed = False

        # 메리츠 환율 업데이트 (보유중 = G열 공란)
        ws_us = wb['메리츠_매매일지']
        tickers = []
        for row in range(2, ws_us.max_row + 1):
            ticker = ws_us.cell(row, 3).value
            sold   = ws_us.cell(row, 7).value   # G열 매도가
            if ticker and not sold:
                tickers.append((row, ticker))

        if tickers:
            syms = list({t for _, t in tickers})
            data = yf.download(syms, period='2d', progress=False, auto_adjust=True)
            for row, ticker in tickers:
                try:
                    price = float(data['Close'][ticker].iloc[-1]) if len(syms) > 1 else float(data['Close'].iloc[-1])
                    # 현재가는 별도 열 없으므로 메모에 기록 (H열)
                    ws_us.cell(row, 9).value = rate   # 환율 최신화
                    changed = True
                except Exception:
                    continue

        if changed:
            wb.save(EXCEL_PATH)
            print(f'  현재가 업데이트 완료 ({datetime.now().strftime("%H:%M:%S")})')

    except Exception as e:
        print(f'  [현재가 오류] {e}')


# ── Watchdog ────────────────────────────────────────────────────────
class ExcelHandler(FileSystemEventHandler):
    def __init__(self):
        self._last = 0

    def on_modified(self, event):
        if event.src_path.endswith('GMCapital_투자일지.xlsx'):
            now = time.time()
            if now - self._last < 5:   # 5초 중복 방지
                return
            self._last = now
            print(f'  [감지] 파일 변경 → 분석 시작')
            threading.Thread(target=process_excel, daemon=True).start()


# ── 1시간 가격 업데이트 루프 ─────────────────────────────────────────
def _price_loop():
    while True:
        time.sleep(3600)
        print(f'[{datetime.now().strftime("%H:%M")}] 현재가 업데이트 중...')
        update_prices()


# ── 메인 ────────────────────────────────────────────────────────────
def main():
    # 엑셀 없으면 생성
    if not os.path.exists(EXCEL_PATH):
        print('엑셀 파일 생성 중...')
        create_excel()

    # 시작 시 미분석 행 한 번 처리
    print('미분석 행 초기 점검...')
    process_excel()

    # 1시간 가격 업데이트 백그라운드
    threading.Thread(target=_price_loop, daemon=True).start()

    # Watchdog 감시 시작
    observer = Observer()
    observer.schedule(ExcelHandler(), path=os.path.dirname(EXCEL_PATH), recursive=False)
    observer.start()
    print(f'OneDrive 감시 시작: {EXCEL_PATH}')
    print('엑셀에 매매 정보 입력하면 자동 분석됩니다. 종료: Ctrl+C')

    try:
        while True:
            time.sleep(10)
    except KeyboardInterrupt:
        observer.stop()
    observer.join()


if __name__ == '__main__':
    main()
