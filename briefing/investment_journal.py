"""
GM Capital 투자일지 v2
거래기록(입력) → 포지션 자동 집계 → 대시보드
"""
import json, os, re, time, threading, traceback
from datetime import datetime, date

import openpyxl
from openpyxl.styles import PatternFill, Font, Alignment, Border, Side
from openpyxl.styles.numbers import FORMAT_NUMBER_COMMA_SEPARATED1
from openpyxl.utils import get_column_letter
from openpyxl.chart import BarChart, Reference
from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler
import yfinance as yf
import requests

import groq_client

BASE_DIR   = os.path.dirname(os.path.abspath(__file__))
ONEDRIVE   = r'C:\Users\DeskTop\OneDrive\문서'
EXCEL_PATH = os.path.join(ONEDRIVE, 'GMCapital_투자일지.xlsx')

with open(os.path.join(BASE_DIR, 'config.json'), 'r', encoding='utf-8') as f:
    _cfg = json.load(f)

# ── 컬러 (밝고 깔끔한 프로 스타일) ─────────────────────────────────
W       = 'FFFFFF'
LIGHT   = 'F8FAFC'
ALT     = 'F1F5F9'
H_NAVY  = '1E3A5F'
H_NAVY2 = '243B53'
GOLD    = 'C9A84C'
AI_BG   = 'EBF4FF'
FB_BG   = 'FFF9EC'
G_BG    = 'DCFCE7'
R_BG    = 'FEE2E2'
G_TXT   = '15803D'
R_TXT   = 'DC2626'
DARK    = '1E293B'
MID     = '475569'
BORDER  = 'CBD5E1'
GOLD_TXT= 'B45309'

def _f(c):   return PatternFill('solid', fgColor=c)
def _fnt(bold=False, color=DARK, size=10, italic=False):
    return Font(bold=bold, color=color, size=size, name='맑은 고딕', italic=italic)
def _al(h='center', v='center', wrap=True):
    return Alignment(horizontal=h, vertical=v, wrap_text=wrap)
def _bd(color=BORDER):
    s = Side(style='thin', color=color)
    return Border(left=s, right=s, top=s, bottom=s)
def _hdr_bd():
    s = Side(style='medium', color='1A3050')
    return Border(left=s, right=s, top=s, bottom=s)


# ══════════════════════════════════════════════════════════
# 엑셀 빌드
# ══════════════════════════════════════════════════════════
def create_excel():
    wb = openpyxl.Workbook()
    wb.remove(wb.active)
    _build_trades(wb)
    _build_position(wb, '미래에셋_포지션', '₩', 'C9A84C')
    _build_position(wb, '메리츠_포지션',   '$', '3B82F6')
    _build_dashboard(wb)
    _build_history(wb)
    wb.save(EXCEL_PATH)
    print(f'  생성 완료: {EXCEL_PATH}')


# ── 거래기록 ─────────────────────────────────────────────
TRADE_COLS = [
    ('날짜', 12), ('계좌', 13), ('구분', 8),
    ('티커', 10), ('종목명', 22),
    ('가격', 14), ('수량', 8), ('메모', 24),
]

def _build_trades(wb):
    ws = wb.create_sheet('거래기록')
    ws.sheet_view.showGridLines = False
    ws.freeze_panes = 'A2'
    ws.sheet_properties.tabColor = '64748B'

    # 헤더
    for ci, (name, width) in enumerate(TRADE_COLS, 1):
        c = ws.cell(1, ci, name)
        c.fill = _f(H_NAVY); c.font = _fnt(bold=True, color=GOLD, size=10)
        c.alignment = _al(); c.border = _hdr_bd()
        ws.column_dimensions[get_column_letter(ci)].width = width
    ws.row_dimensions[1].height = 24

    # 데이터 행 (200행 미리 스타일)
    for row in range(2, 201):
        bg = LIGHT if row % 2 == 0 else W
        for ci in range(1, len(TRADE_COLS) + 1):
            c = ws.cell(row, ci)
            c.fill = _f(bg); c.font = _fnt(color=DARK)
            c.alignment = _al(h='left' if ci in [2,4,5,8] else 'center')
            c.border = _bd()
        ws.row_dimensions[row].height = 18


# ── 포지션 시트 ──────────────────────────────────────────
POS_COLS_KRW = [
    ('티커', 9), ('종목명', 22), ('보유수량', 10),
    ('평균단가(₩)', 14), ('투자금액(₩)', 15),
    ('현재가(₩)', 14), ('평가금액(₩)', 15),
    ('손익(₩)', 14), ('수익률(%)', 11),
    ('매수 핵심 논거', 52), ('리스크 요인', 32),
    ('목표가(₩)', 13), ('손절가(₩)', 13), ('종합판단', 12),
]
POS_COLS_USD = [
    ('티커', 9), ('종목명', 22), ('보유수량', 10),
    ('평균단가($)', 14), ('투자금액($)', 15),
    ('현재가($)', 14), ('평가금액($)', 15),
    ('손익($)', 14), ('수익률(%)', 11),
    ('매수 핵심 논거', 52), ('리스크 요인', 32),
    ('목표가($)', 13), ('손절가($)', 13), ('종합판단', 12),
]

def _build_position(wb, name, currency, tab):
    ws = wb.create_sheet(name)
    ws.sheet_view.showGridLines = False
    ws.freeze_panes = 'A2'
    ws.sheet_properties.tabColor = tab

    cols = POS_COLS_KRW if currency == '₩' else POS_COLS_USD

    # 섹션 레이블 (배경색으로 구분)
    section_bg = {
        **{i: H_NAVY  for i in range(1,10)},    # A~I: 포지션 데이터 (헤더는 네이비)
        **{i: H_NAVY2 for i in range(10,13)},   # J~L: AI 분석
        **{i: H_NAVY  for i in range(13,15)},   # M~N: 판단
    }

    for ci, (col_name, width) in enumerate(cols, 1):
        c = ws.cell(1, ci, col_name)
        c.fill = _f(section_bg.get(ci, H_NAVY))
        c.font = _fnt(bold=True, color=GOLD if ci in range(10,13) else W, size=10)
        c.alignment = _al(); c.border = _hdr_bd()
        ws.column_dimensions[get_column_letter(ci)].width = width
    ws.row_dimensions[1].height = 26

    for row in range(2, 101):
        for ci in range(1, len(cols) + 1):
            c = ws.cell(row, ci)
            if ci <= 9:     c.fill = _f(W)
            elif ci <= 12:  c.fill = _f(AI_BG)
            else:           c.fill = _f(FB_BG)
            c.font = _fnt(color=DARK)
            c.alignment = _al(h='left' if ci in [2,10,11] else 'center')
            c.border = _bd()
        ws.row_dimensions[row].height = 56


# ── 대시보드 ─────────────────────────────────────────────
def _build_dashboard(wb):
    ws = wb.create_sheet('대시보드')
    ws.sheet_view.showGridLines = False
    ws.sheet_properties.tabColor = '10B981'

    # 전체 배경
    for r in range(1, 60):
        for c in range(1, 22):
            ws.cell(r, c).fill = _f(LIGHT)

    # ── 타이틀 바 ──
    ws.merge_cells('A1:U1')
    t = ws['A1']
    t.value = 'GM Capital  Investment Dashboard'
    t.fill  = _f(H_NAVY)
    t.font  = Font(bold=True, color=GOLD, size=15, name='맑은 고딕')
    t.alignment = _al()
    ws.row_dimensions[1].height = 34

    # ── 계좌 레이블 ──
    ws.merge_cells('A2:U2')
    ws['A2'].fill = _f('E2E8F0')

    # ── 요약 카드 (행 4~7) ──
    cards = [
        ('B4', '총 포트폴리오 (₩)', 'total_portfolio'),
        ('F4', '총 평가손익 (₩)',   'total_pnl'),
        ('J4', '전체 수익률',        'total_ret'),
        ('N4', '보유 종목 수',       'total_pos'),
        ('R4', '업데이트',           'last_update'),
    ]
    for addr, label, key in cards:
        c = ws[addr]
        c.value = label
        c.fill  = _f(H_NAVY); c.font = _fnt(bold=True, color=GOLD, size=9)
        c.alignment = _al(); c.border = _hdr_bd()
        ws.row_dimensions[4].height = 20

        val_addr = addr[0] + str(int(addr[1:]) + 2)
        ws.merge_cells(f'{addr}:{chr(ord(addr[0])+2)}{addr[1:]}')
        ws.merge_cells(f'{val_addr}:{chr(ord(val_addr[0])+2)}{val_addr[1:]}')

    # ── 미래에셋 섹션 헤더 (행 10) ──
    ws.merge_cells('A10:G10')
    h1 = ws['A10']
    h1.value = '  미래에셋 (국내주식)'
    h1.fill  = _f(H_NAVY); h1.font = _fnt(bold=True, color=W, size=11)
    h1.alignment = _al(h='left'); ws.row_dimensions[10].height = 24

    # 미래에셋 포지션 테이블 헤더
    dom_hdr = ['티커','종목명','보유수량','평균단가(₩)','현재가(₩)','손익(₩)','수익률(%)']
    for ci, h in enumerate(dom_hdr, 1):
        c = ws.cell(11, ci, h)
        c.fill = _f(H_NAVY2); c.font = _fnt(bold=True, color=W, size=9)
        c.alignment = _al(); c.border = _hdr_bd()
    ws.row_dimensions[11].height = 20

    for r in range(12, 22):
        for ci in range(1, 8):
            c = ws.cell(r, ci)
            c.fill = _f(W if r % 2 == 0 else LIGHT)
            c.font = _fnt(color=DARK, size=10)
            c.alignment = _al(h='left' if ci == 2 else 'center')
            c.border = _bd()
        ws.row_dimensions[r].height = 20

    # ── 메리츠 섹션 헤더 (행 10, 열 9~) ──
    ws.merge_cells('I10:P10')
    h2 = ws['I10']
    h2.value = '  메리츠 (해외주식)'
    h2.fill  = _f('1D4ED8'); h2.font = _fnt(bold=True, color=W, size=11)
    h2.alignment = _al(h='left')

    us_hdr = ['티커','종목명','보유수량','평균단가($)','현재가($)','손익($)','수익률(%)','환율']
    for ci, h in enumerate(us_hdr, 9):
        c = ws.cell(11, ci, h)
        c.fill = _f('1E40AF'); c.font = _fnt(bold=True, color=W, size=9)
        c.alignment = _al(); c.border = _hdr_bd()

    for r in range(12, 22):
        for ci in range(9, 17):
            c = ws.cell(r, ci)
            c.fill = _f(W if r % 2 == 0 else 'EFF6FF')
            c.font = _fnt(color=DARK, size=10)
            c.alignment = _al(h='left' if ci == 10 else 'center')
            c.border = _bd()

    ws.column_dimensions['A'].width = 9
    ws.column_dimensions['B'].width = 22
    for col in 'CDEFG': ws.column_dimensions[col].width = 14
    ws.column_dimensions['H'].width = 2
    ws.column_dimensions['I'].width = 9
    ws.column_dimensions['J'].width = 22
    for col in 'KLMNOP': ws.column_dimensions[col].width = 14


def _build_history(wb):
    ws = wb.create_sheet('분석히스토리')
    ws.sheet_view.showGridLines = False
    ws.sheet_properties.tabColor = '94A3B8'
    cols = [('분석일시',18),('계좌',12),('티커',10),('구분',12),('핵심논거',80)]
    for ci, (n, w) in enumerate(cols, 1):
        c = ws.cell(1, ci, n)
        c.fill = _f(H_NAVY); c.font = _fnt(bold=True, color=W, size=10)
        c.alignment = _al(); c.border = _hdr_bd()
        ws.column_dimensions[get_column_letter(ci)].width = w
    ws.row_dimensions[1].height = 22
    for r in range(2, 201):
        bg = LIGHT if r % 2 == 0 else W
        for ci in range(1, 6):
            c = ws.cell(r, ci)
            c.fill = _f(bg); c.font = _fnt(color=DARK, size=9)
            c.alignment = _al(h='left' if ci == 5 else 'center')
            c.border = _bd()


# ══════════════════════════════════════════════════════════
# 포지션 계산 (거래기록 → 집계)
# ══════════════════════════════════════════════════════════
def calc_positions(wb, account):
    """거래기록에서 계좌별 포지션 계산"""
    ws = wb['거래기록']
    pos = {}

    for row in range(2, ws.max_row + 1):
        acct   = ws.cell(row, 2).value
        if acct != account:
            continue
        kind   = str(ws.cell(row, 3).value or '')
        ticker = ws.cell(row, 4).value
        name   = ws.cell(row, 5).value
        price  = ws.cell(row, 6).value
        qty    = ws.cell(row, 7).value

        if not ticker or not price or not qty:
            continue

        if ticker not in pos:
            pos[ticker] = {'name': name or ticker, 'qty': 0, 'cost': 0.0}

        if '매수' in kind:
            old_qty  = pos[ticker]['qty']
            old_cost = pos[ticker]['cost']
            new_qty  = old_qty + qty
            pos[ticker]['qty']  = new_qty
            pos[ticker]['cost'] = (old_cost * old_qty + price * qty) / new_qty
        elif '매도' in kind:
            pos[ticker]['qty'] = max(0, pos[ticker]['qty'] - qty)
            if pos[ticker]['qty'] == 0:
                pos[ticker]['cost'] = 0.0

    return {k: v for k, v in pos.items() if v['qty'] > 0}


# ══════════════════════════════════════════════════════════
# 현재가 조회
# ══════════════════════════════════════════════════════════
def _get_usd_krw():
    try:
        h = yf.Ticker('USDKRW=X').history(period='2d')
        return round(float(h['Close'].iloc[-1]), 0) if not h.empty else 1380.0
    except Exception:
        return 1380.0

def _get_prices(tickers):
    """티커 리스트 → {ticker: price} 딕셔너리"""
    if not tickers:
        return {}
    try:
        data = yf.download(list(tickers), period='2d',
                           progress=False, auto_adjust=True)
        prices = {}
        for t in tickers:
            try:
                col = data['Close'][t] if len(tickers) > 1 else data['Close']
                prices[t] = round(float(col.dropna().iloc[-1]), 4)
            except Exception:
                prices[t] = 0.0
        return prices
    except Exception:
        return {t: 0.0 for t in tickers}

def _get_kr_price(ticker):
    """국내 ETF 현재가 (원화)"""
    try:
        code = f'{str(ticker).zfill(6)}.KS'
        h = yf.Ticker(code).history(period='2d')
        if not h.empty:
            return round(float(h['Close'].iloc[-1]), 0)
    except Exception:
        pass
    return 0


# ══════════════════════════════════════════════════════════
# Groq 분석
# ══════════════════════════════════════════════════════════
def _news(ticker):
    try:
        import xml.etree.ElementTree as ET
        url = (f'https://news.google.com/rss/search?q={ticker}+stock'
               f'&hl=en-US&gl=US&ceid=US:en')
        resp = requests.get(url, timeout=8, headers={'User-Agent': 'Mozilla/5.0'})
        titles = [i.find('title').text.strip()
                  for i in ET.fromstring(resp.content).findall('.//item')[:4]
                  if i.find('title') is not None]
        return ' / '.join(titles[:3]) or '뉴스 없음'
    except Exception:
        return '뉴스 없음'

def _price_info(ticker):
    try:
        info = yf.Ticker(ticker).info
        return {
            'target': round(info.get('targetMeanPrice') or 0, 2),
            'rec':    info.get('recommendationKey', ''),
            'pe':     round(info.get('trailingPE') or 0, 1),
            'sector': info.get('sector', ''),
        }
    except Exception:
        return {}

_DOM_PROMPT = """\
국내 ETF/주식 투자 분석.

종목: {name} (코드 {ticker})
평균매수가: {price:,}원 | 보유수량: {qty}주 | 투자금: {amount:,}원
현재가: {current:,}원 | 수익률: {ret:+.1f}%
최근 뉴스: {news}

JSON 형식만 응답:
{{
  "핵심논거": "지금 이 종목을 계속 보유해야 할 구체적 이유 — 매크로 환경, 섹터 트렌드, ETF 특성 등 근거 3가지 포함. 250자 이내",
  "리스크": "1. 첫번째 리스크\\n2. 두번째 리스크  (각 50자 이내)",
  "목표가": 숫자(원),
  "손절가": 숫자(원),
  "종합판단": "매수적절 or 주의 or 부적절"
}}
한자 금지. 단정 금지. 분석가 어조."""

_US_PROMPT = """\
미국 주식/ETF 투자 분석.

종목: {name} ({ticker})
평균매수가: ${price} | 보유수량: {qty}주 | 투자금: ${amount:,.0f}
현재가: ${current} | 수익률: {ret:+.1f}%
애널리스트 목표주가: ${target} | 추천: {rec} | 섹터: {sector} | PER: {pe}
최근 뉴스: {news}

JSON 형식만 응답:
{{
  "핵심논거": "지금 이 종목을 계속 보유해야 할 구체적 이유 — 매크로 환경, 기업 펀더멘털, 섹터 트렌드, 밸류에이션 관점 포함. 근거 3가지 이상. 280자 이내",
  "리스크": "1. 첫번째 리스크\\n2. 두번째 리스크  (각 60자 이내)",
  "목표가": 숫자(달러),
  "손절가": 숫자(달러),
  "종합판단": "매수적절 or 주의 or 부적절"
}}
한자 금지. 단정 금지. 분석가 어조."""


def _run_analysis(is_us, ticker, name, avg_price, qty, current, news_str, pinfo):
    ret = (current - avg_price) / avg_price * 100 if avg_price else 0
    if is_us:
        prompt = _US_PROMPT.format(
            ticker=ticker, name=name, price=avg_price, qty=qty,
            amount=avg_price * qty, current=current, ret=ret,
            target=pinfo.get('target',0), rec=pinfo.get('rec','미제공'),
            sector=pinfo.get('sector','미제공'), pe=pinfo.get('pe',0),
            news=news_str)
    else:
        prompt = _DOM_PROMPT.format(
            ticker=ticker, name=name, price=int(avg_price), qty=qty,
            amount=int(avg_price * qty), current=int(current), ret=ret,
            news=news_str)
    try:
        raw = groq_client.call(prompt, max_tokens=700, temperature=0.4)
        m   = re.search(r'\{.*\}', raw, re.DOTALL)
        return json.loads(m.group()) if m else {}
    except Exception as e:
        print(f'    [분석 오류 {ticker}] {e}')
        return {}


# ══════════════════════════════════════════════════════════
# 포지션 시트 기입
# ══════════════════════════════════════════════════════════
def _pnl_style(ws, row, pnl):
    bg  = G_BG if pnl >= 0 else R_BG
    txt = G_TXT if pnl >= 0 else R_TXT
    for ci in [8, 9]:
        c = ws.cell(row, ci)
        c.fill = _f(bg)
        c.font = _fnt(bold=True, color=txt)

def _judgment_style(ws, row, val):
    color = G_TXT if val == '매수적절' else (R_TXT if val == '부적절' else GOLD_TXT)
    bg    = G_BG  if val == '매수적절' else (R_BG  if val == '부적절' else FB_BG)
    c = ws.cell(row, 14)
    c.fill = _f(bg); c.font = _fnt(bold=True, color=color)

def _get_existing_analysis(ws, ticker):
    """이미 분석된 종목이면 기존 데이터 반환"""
    for row in range(2, ws.max_row + 1):
        if ws.cell(row, 1).value == ticker and ws.cell(row, 10).value:
            return {
                '핵심논거': ws.cell(row, 10).value,
                '리스크':   ws.cell(row, 11).value,
                '목표가':   ws.cell(row, 12).value,
                '손절가':   ws.cell(row, 13).value,
                '종합판단': ws.cell(row, 14).value,
            }
    return None

def write_positions(wb, account, is_us, prices, rate):
    sheet_name = '메리츠_포지션' if is_us else '미래에셋_포지션'
    ws = wb[sheet_name]
    positions = calc_positions(wb, account)

    # 기존 분석 캐시 저장
    cache = {}
    for row in range(2, ws.max_row + 1):
        t = ws.cell(row, 1).value
        if t and ws.cell(row, 10).value:
            cache[t] = {
                '핵심논거': ws.cell(row, 10).value,
                '리스크':   ws.cell(row, 11).value,
                '목표가':   ws.cell(row, 12).value,
                '손절가':   ws.cell(row, 13).value,
                '종합판단': ws.cell(row, 14).value,
            }

    # 시트 데이터 행 초기화
    for row in range(2, 101):
        for ci in range(1, 15):
            c = ws.cell(row, ci)
            c.value = None
            if ci <= 9:     c.fill = _f(W)
            elif ci <= 13:  c.fill = _f(AI_BG)
            else:           c.fill = _f(FB_BG)
            c.font = _fnt(color=DARK)
            c.alignment = _al(h='left' if ci in [2,10,11] else 'center')
            c.border = _bd()

    write_row = 2
    for ticker, pdata in positions.items():
        name     = pdata['name']
        qty      = pdata['qty']
        avg      = round(pdata['cost'], 4)
        current  = prices.get(ticker, 0)
        if current == 0:
            # 국내는 .KS 시도
            if not is_us:
                current = _get_kr_price(ticker)
        pnl      = round((current - avg) * qty, 2)
        pnl_pct  = round((current - avg) / avg * 100, 2) if avg else 0
        invested = round(avg * qty, 2)
        cur_val  = round(current * qty, 2)

        ws.cell(write_row, 1, ticker)
        ws.cell(write_row, 2, name)
        ws.cell(write_row, 3, qty)
        ws.cell(write_row, 4, avg)
        ws.cell(write_row, 5, invested)
        ws.cell(write_row, 6, current if current else '-')
        ws.cell(write_row, 7, cur_val if current else '-')
        ws.cell(write_row, 8, pnl if current else '-')
        ws.cell(write_row, 9, pnl_pct if current else '-')

        if pnl_pct:
            _pnl_style(ws, write_row, pnl)

        # 분석 (캐시 우선, 없으면 새로 실행)
        analysis = cache.get(ticker)
        if not analysis:
            print(f'    [분석] {ticker}')
            news_str = _news(ticker) if is_us else '국내 ETF'
            pinfo    = _price_info(ticker) if is_us else {}
            analysis = _run_analysis(is_us, ticker, name, avg, qty,
                                     current or avg, news_str, pinfo)
        else:
            print(f'    [캐시] {ticker}')

        if analysis:
            ws.cell(write_row, 10, analysis.get('핵심논거', ''))
            ws.cell(write_row, 11, analysis.get('리스크', ''))
            ws.cell(write_row, 12, analysis.get('목표가', ''))
            ws.cell(write_row, 13, analysis.get('손절가', ''))
            ws.cell(write_row, 14, analysis.get('종합판단', ''))
            _judgment_style(ws, write_row, str(analysis.get('종합판단','')))

            # 분석히스토리 기록 (캐시 아닌 경우만)
            if ticker not in cache:
                _write_history(wb, account, ticker, analysis.get('핵심논거',''))

        ws.row_dimensions[write_row].height = 70
        write_row += 1

    return positions


# ══════════════════════════════════════════════════════════
# 대시보드 업데이트
# ══════════════════════════════════════════════════════════
def update_dashboard(wb, dom_pos, us_pos, dom_prices, us_prices, rate):
    ws = wb['대시보드']

    # 미래에셋 포지션 행 채우기
    row = 12
    dom_total_inv = dom_total_cur = 0
    for ticker, p in dom_pos.items():
        avg     = p['cost']
        qty     = p['qty']
        current = dom_prices.get(ticker, 0) or _get_kr_price(ticker)
        pnl     = (current - avg) * qty
        pct     = (current - avg) / avg * 100 if avg else 0
        dom_total_inv += avg * qty
        dom_total_cur += current * qty

        data = [ticker, p['name'], qty,
                f'{avg:,.0f}', f'{current:,.0f}' if current else '-',
                f'{pnl:,.0f}' if current else '-',
                f'{pct:+.2f}%' if current else '-']
        for ci, val in enumerate(data, 1):
            c = ws.cell(row, ci, val)
            c.font = _fnt(color=DARK, size=10)
            c.alignment = _al(h='left' if ci == 2 else 'center')
            c.border = _bd()
            if ci in [6, 7] and current:
                c.fill = _f(G_BG if pnl >= 0 else R_BG)
                c.font = _fnt(bold=True, color=G_TXT if pnl >= 0 else R_TXT, size=10)
        row += 1

    # 메리츠 포지션 행 채우기
    row = 12
    us_total_inv_usd = us_total_cur_usd = 0
    for ticker, p in us_pos.items():
        avg     = p['cost']
        qty     = p['qty']
        current = us_prices.get(ticker, 0)
        pnl     = (current - avg) * qty
        pct     = (current - avg) / avg * 100 if avg else 0
        us_total_inv_usd += avg * qty
        us_total_cur_usd += current * qty

        data = [ticker, p['name'], qty,
                f'${avg:,.2f}', f'${current:,.2f}' if current else '-',
                f'${pnl:,.2f}' if current else '-',
                f'{pct:+.2f}%' if current else '-',
                f'{rate:,.0f}']
        for ci, val in enumerate(data, 9):
            c = ws.cell(row, ci, val)
            c.font = _fnt(color=DARK, size=10)
            c.alignment = _al(h='left' if ci == 10 else 'center')
            c.border = _bd()
            if ci in [14, 15] and current:
                c.fill = _f(G_BG if pnl >= 0 else R_BG)
                c.font = _fnt(bold=True, color=G_TXT if pnl >= 0 else R_TXT, size=10)
        row += 1

    # 요약 카드 값 채우기
    dom_pnl    = dom_total_cur - dom_total_inv
    us_pnl_krw = (us_total_cur_usd - us_total_inv_usd) * rate
    total_inv  = dom_total_inv + us_total_inv_usd * rate
    total_cur  = dom_total_cur + us_total_cur_usd * rate
    total_pnl  = total_cur - total_inv
    total_ret  = total_pnl / total_inv * 100 if total_inv else 0
    n_pos      = len(dom_pos) + len(us_pos)

    summary = [
        ('B6', f'{total_cur:,.0f}원'),
        ('F6', f'{total_pnl:+,.0f}원'),
        ('J6', f'{total_ret:+.2f}%'),
        ('N6', f'{n_pos}종목'),
        ('R6', datetime.now().strftime('%m/%d %H:%M')),
    ]
    for addr, val in summary:
        c = ws[addr]
        c.value = val
        is_pnl  = addr in ['F6','J6']
        is_pos  = total_pnl >= 0
        c.font  = Font(bold=True,
                       color=(G_TXT if is_pos else R_TXT) if is_pnl else H_NAVY,
                       size=13, name='맑은 고딕')
        c.fill  = _f(W)
        c.alignment = _al()
        c.border = _hdr_bd()


# ══════════════════════════════════════════════════════════
# 분석히스토리 기록
# ══════════════════════════════════════════════════════════
def _write_history(wb, account, ticker, content):
    ws  = wb['분석히스토리']
    row = 2
    while ws.cell(row, 1).value:
        row += 1
    data = [datetime.now().strftime('%Y-%m-%d %H:%M'), account, ticker, '포지션분석', content]
    bg   = LIGHT if row % 2 == 0 else W
    for ci, val in enumerate(data, 1):
        c = ws.cell(row, ci, val)
        c.fill = _f(bg); c.font = _fnt(color=DARK, size=9)
        c.alignment = _al(h='left' if ci == 5 else 'center')
        c.border = _bd()


# ══════════════════════════════════════════════════════════
# 전체 처리
# ══════════════════════════════════════════════════════════
_lock = threading.Lock()

def process_all():
    if not os.path.exists(EXCEL_PATH):
        create_excel()
        return

    with _lock:
        try:
            wb   = openpyxl.load_workbook(EXCEL_PATH)
            rate = _get_usd_krw()
            print(f'  환율: {rate:,.0f}원')

            # 포지션 계산
            dom_pos = calc_positions(wb, '미래에셋')
            us_pos  = calc_positions(wb, '메리츠')

            # 현재가 조회
            dom_prices = {t: _get_kr_price(t) for t in dom_pos}
            us_prices  = _get_prices(list(us_pos.keys())) if us_pos else {}

            print(f'  미래에셋 {len(dom_pos)}종목 / 메리츠 {len(us_pos)}종목')

            # 포지션 시트 업데이트
            write_positions(wb, '미래에셋', False, dom_prices, rate)
            write_positions(wb, '메리츠',   True,  us_prices,  rate)

            # 대시보드 업데이트
            update_dashboard(wb, dom_pos, us_pos, dom_prices, us_prices, rate)

            wb.save(EXCEL_PATH)
            print(f'  저장 완료 ({datetime.now().strftime("%H:%M:%S")})')

        except Exception as e:
            print(f'  [오류] {e}')
            traceback.print_exc()


# ══════════════════════════════════════════════════════════
# Watchdog + 주기 업데이트
# ══════════════════════════════════════════════════════════
class ExcelHandler(FileSystemEventHandler):
    def __init__(self):
        self._last = 0
    def on_modified(self, event):
        if 'GMCapital_투자일지' in event.src_path:
            now = time.time()
            if now - self._last < 8:
                return
            self._last = now
            print(f'\n[감지] 파일 변경 → 재처리')
            threading.Thread(target=process_all, daemon=True).start()

def _price_loop():
    while True:
        time.sleep(3600)
        print(f'\n[{datetime.now().strftime("%H:%M")}] 정기 가격 업데이트')
        process_all()


def _init_holdings(wb):
    """기존 보유 종목 초기 데이터 (거래기록 시트가 빈 경우에만)"""
    ws = wb['거래기록']
    if ws.cell(2, 1).value:
        return
    today = '2026-06-07'
    rows = [
        # 미래에셋 — 가격·수량 미기재, 이사장님이 직접 입력
        (today, '미래에셋', '매수', '133690', 'TIGER 미국나스닥100',    None, None, '초기보유 — 가격·수량 직접 입력'),
        (today, '미래에셋', '매수', '360750', 'TIGER 미국S&P500',       None, None, '초기보유 — 가격·수량 직접 입력'),
        (today, '미래에셋', '매수', '453850', 'ACE 미국30년국채액티브', None, None, '초기보유 — 가격·수량 직접 입력'),
        (today, '미래에셋', '매수', '232080', 'TIGER 코스닥150',        None, None, '초기보유 — 가격·수량 직접 입력'),
        # 메리츠 — 평균단가($/주) 기입
        (today, '메리츠', '매수', 'MU',   'Micron Technology',         354.23,   2, '초기보유'),
        (today, '메리츠', '매수', 'IEMG', 'iShares Core MSCI EM',       70.5376, 13, '초기보유'),
        (today, '메리츠', '매수', 'SPYM', 'SPDR S&P 500 Momentum',      80.7258, 24, '초기보유'),
        (today, '메리츠', '매수', 'GGLL', 'GraniteShares 2x GOOGL',    113.7276, 17, '초기보유'),
        (today, '메리츠', '매수', 'QLD',  'ProShares Ultra QQQ',        85.042,  24, '초기보유'),
        (today, '메리츠', '매수', 'TSM',  'Taiwan Semiconductor ADR',  406.74,    1, '초기보유'),
    ]
    for i, row_data in enumerate(rows, 2):
        bg = LIGHT if i % 2 == 0 else W
        for ci, val in enumerate(row_data, 1):
            c = ws.cell(i, ci)
            if val is not None:
                c.value = val
            c.fill = _f(bg)
            c.font = _fnt(color=MID if val is None else DARK)
            c.alignment = _al(h='left' if ci in [2, 4, 5, 8] else 'center')
            c.border = _bd()


def main():
    # 거래기록 시트가 없으면 새 구조로 재생성
    needs_create = True
    if os.path.exists(EXCEL_PATH):
        try:
            wb_check = openpyxl.load_workbook(EXCEL_PATH, read_only=True)
            needs_create = '거래기록' not in wb_check.sheetnames
            wb_check.close()
        except Exception:
            pass

    if needs_create:
        print('새 엑셀 구조 생성 중...')
        wb = openpyxl.Workbook()
        wb.remove(wb.active)
        _build_trades(wb)
        _build_position(wb, '미래에셋_포지션', '₩', 'C9A84C')
        _build_position(wb, '메리츠_포지션',   '$',  '3B82F6')
        _build_dashboard(wb)
        _build_history(wb)
        _init_holdings(wb)
        wb.save(EXCEL_PATH)
        print(f'  완료: {EXCEL_PATH}')

    print('초기 처리 중...')
    process_all()

    threading.Thread(target=_price_loop, daemon=True).start()

    observer = Observer()
    observer.schedule(ExcelHandler(), path=ONEDRIVE, recursive=False)
    observer.start()
    print(f'감시 시작. 종료: Ctrl+C')
    try:
        while True:
            time.sleep(10)
    except KeyboardInterrupt:
        observer.stop()
    observer.join()


if __name__ == '__main__':
    main()
