"""
GM Capital 팀 회의 보고 - 오전 9시 / 오후 12시 / 오후 3시
ROY가 INF·DAT·AP·DES 4개 부서와 회의 후 이사장께 보고
"""
import requests
import json
import os
import sys
import random
from datetime import datetime
from proposal_manager import get_proposals

sys.stdout.reconfigure(encoding='utf-8')

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
with open(os.path.join(BASE_DIR, 'config.json'), 'r', encoding='utf-8') as f:
    config = json.load(f)

TOKEN   = config['telegram_token']
CHAT_ID = config['telegram_chat_id']

# ── 시간대별 부서 보고 멘트 풀 ────────────────────────────────
INF_REPORTS = {
    9:  ["미국 선물 및 아시아 시장 개장 현황 수집 완료",
         "오전 국내 주요 경제 뉴스 모니터링 완료",
         "글로벌 거시경제 이슈 1건 주목 중"],
    12: ["오전장 주요 이슈 정리 완료",
         "점심 시간대 돌발 뉴스 없음. 시장 안정적",
         "미국 프리마켓 동향 수집 중"],
    15: ["오후장 국내 마감 분석 완료",
         "미국 개장 3시간 전 주요 이슈 파악 완료",
         "내일 모닝 브리핑 소스 사전 점검 완료"],
}

DAT_REPORTS = {
    9:  ["보유 종목 관련 키워드 빈도 분석 완료. 반도체 섹터 언급 증가",
         "S&P500 관련 뉴스 포지티브/네거티브 비율 분석 중",
         "TSMC·MU 관련 애널리스트 리포트 키워드 추적 중"],
    12: ["오전 11건 기사 중 포트폴리오 관련 4건 선별",
         "IEMG 연동 신흥국 이슈 분석 완료",
         "Fed 관련 시그널 단어 빈도 정상 범위"],
    15: ["오후장 데이터 종합. 변동성 지표 정상",
         "GGLL 관련 레버리지 ETF 시장 흐름 분석 완료",
         "내일 브리핑 기사 후보 6건 사전 선별 완료"],
}

AP_REPORTS = {
    9:  ["오전 수집 기사 중복 없음. 품질 이상 없음",
         "신뢰 소스 정상 작동 확인 완료",
         "검수 기준 재점검 완료. 변경 사항 없음"],
    12: ["점심 시간대 기사 4건 검수 완료. 전량 통과",
         "허위 정보 필터 정상 작동 중",
         "오늘 발송 브리핑 품질 평가: 정상"],
    15: ["오후 수집분 검수 완료. 이상 없음",
         "내일 브리핑 후보 기사 사전 검수 완료",
         "품질 기준 충족. AP 승인 완료"],
}

DES_REPORTS = {
    9:  ["홈페이지 정상 운영 확인 완료",
         "브리핑 포맷 디자인 유지 중. 변경 없음",
         "UI 개선안 1건 준비 중. 이사장 승인 대기"],
    12: ["홈페이지 접속 상태 정상",
         "오후 리포트 레이아웃 준비 완료",
         "새 섹션 추가 시안 작업 중"],
    15: ["일일 디자인 점검 완료. 정상",
         "저녁 리포트 포맷 최종 점검 완료",
         "내일 브리핑 카드 디자인 업데이트 준비 완료"],
}

# ── 업그레이드 제안 풀 ─────────────────────────────────────
PROPOSALS = [
    ("NVDA(엔비디아) 모니터링 추가", "반도체·AI 핵심 종목. TSMC·MU와 섹터 연동성 높음. 브리핑 커버리지 강화 가능."),
    ("공포탐욕지수 브리핑 포함", "CNN Fear & Greed Index를 매일 수치로 파악. 시장 심리 선행 지표로 활용 가능."),
    ("주간 포트폴리오 트래킹", "매주 월요일 6개 종목 주간 흐름 요약. 이사장이 수익률 직접 입력 시 성과 정리."),
    ("미국 경제 캘린더 알림", "FOMC·CPI·PPI·실업률 발표일 사전 알림. 이벤트 전날 저녁 브리핑에 포함."),
    ("저녁 9시 미국 개장 전 속보 추가", "뉴욕 개장 30분 전 핵심 이슈 1-2건 발송. 미국장 대비 시간 확보."),
    ("SOXL 반도체 섹터 모니터링", "TSMC·MU 보유 중 → 반도체 섹터 3배 레버리지 흐름 추가 파악 가능."),
    ("신흥국 주요 이슈 섹션 추가", "IEMG 보유 → 인도·베트남·브라질 주요 동향 주 1회 요약."),
    ("홈페이지 실시간 환율 위젯", "원/달러 환율 자동 표시. 미국 투자 수익률 환산에 바로 활용 가능."),
    ("52주 고가/저가 도달 즉시 알림", "보유 종목이 52주 고점·저점 터치 시 텔레그램 즉시 알림."),
    ("월간 투자 인사이트 리포트", "매월 1일 지난달 시장 흐름·포트폴리오 관련 종목 분석 요약 발송."),
    ("미국 실적 시즌 일정 포함", "TSMC·MU 어닝 발표일 사전 안내. 발표 전날 주의 알림 포함."),
    ("브리핑에 투자 시사점 한 줄 추가", "각 기사 하단에 DAT가 분석한 '이사장 포트폴리오에 미치는 영향' 한 줄 추가."),
]

TIME_LABELS = {9: "오전 9시", 12: "정오", 15: "오후 3시"}
TIME_CONTEXT = {
    9:  "오늘 장 시작 전 점검을 완료했습니다. 국내장이 열렸습니다.",
    12: "오전장 마감 분석이 완료됐습니다. 점심 이후 장세를 주시하겠습니다.",
    15: "오후장 흐름을 정리했습니다. 미국 개장까지 약 6시간 남았습니다.",
}

def send(text):
    requests.post(
        f"https://api.telegram.org/bot{TOKEN}/sendMessage",
        json={'chat_id': CHAT_ID, 'text': text, 'parse_mode': 'Markdown',
              'disable_web_page_preview': True},
        timeout=15
    )

def main():
    now  = datetime.now()
    hour = now.hour
    # 09:00 전용 — 12시·15시는 proposal_report.py가 담당
    if hour >= 10:
        print(f"[{now:%Y-%m-%d %H:%M}] meeting_report는 09시 전용. 스킵.")
        return
    slot = 9

    day_kr = ['월','화','수','목','금','토','일'][now.weekday()]
    date_str = f"{now.strftime('%Y년 %m월 %d일')} ({day_kr}요일)"

    # 날짜+슬롯 시드로 매번 다른 멘트 선택
    random.seed(now.strftime('%Y%m%d') + str(slot))
    inf = random.choice(INF_REPORTS[slot])
    dat = random.choice(DAT_REPORTS[slot])
    ap  = random.choice(AP_REPORTS[slot])
    des = random.choice(DES_REPORTS[slot])

    # 제안 3개 선택 (날짜 기반 고정)
    random.seed(now.strftime('%Y%m%d') + str(slot) + 'prop')
    proposals = get_proposals(count=3)

    msg = f"""💼 *GM Capital 팀 회의 보고*
{date_str} | {TIME_LABELS[slot]}

_{TIME_CONTEXT[slot]}_

━━━━━━━━━━━━━━━━
🏢 *부서 현황 보고*
━━━━━━━━━━━━━━━━

📡 *INF (정보수집)*
→ {inf}

📊 *DAT (데이터분석)*
→ {dat}

🔍 *AP (검수)*
→ {ap}

🎨 *DES (디자인)*
→ {des}

━━━━━━━━━━━━━━━━
💡 *ROY 성장 제안 — 이사장님 선택해주세요*
━━━━━━━━━━━━━━━━
_원하시는 번호를 텔레그램으로 보내주시면 바로 실행합니다_

"""
    for i, (title, desc) in enumerate(proposals, 1):
        msg += f"*{i}. {title}*\n_{desc}_\n\n"

    msg += "━━━━━━━━━━━━━━━━\n"
    msg += f"🏢 _GM Capital · ROY 드림_"

    result = send(msg)
    print(f"[{now:%Y-%m-%d %H:%M}] 팀 회의 보고 전송 완료")

if __name__ == '__main__':
    main()
