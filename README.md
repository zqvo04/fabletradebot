# FableTradeBot

BTC/ETH/SOL/HYPE 무기한 선물 대상 국면 적응형(regime-adaptive) 데이+스윙 트레이딩 시스템.
설계 근거와 수식은 [BLUEPRINT.md](BLUEPRINT.md) 참조.

## 검증 상태 (Phase 3–4, 실데이터 18.5개월)

| 시스템 | 결과 | 리포트 |
|---|---|---|
| 1H (테이커) | **불합격** — 총이익 +7.8k 중 수수료가 10.9k를 잠식 (비용 지배) | [VALIDATION.md](VALIDATION.md) |
| 1H (메이커 재분석) | **기각 확정** — 지정가 경제학(낙관적 상한 포함)으로도 2025 설계 구간 전부 마이너스. 비용이 아니라 트레이드당 엣지(avg R≈0.00) 자체가 부족 | [ANALYSIS_TEMPO.md](ANALYSIS_TEMPO.md) |
| 4H + 메이커 청산 (v1) | 전 게이트 통과 — +1.91%/MDD −4.9% | [VALIDATION_4H.md](VALIDATION_4H.md) |
| **v2 재설계 (최종 채택)** | **전 게이트 통과** — +8.36%/MDD −6.8%, avgR +0.256 (v1의 8배), PF 1.51, 민감도 8코너 +5.7~+11.6%, 비용 2배 +10.2% | [REDESIGN_V2.md](REDESIGN_V2.md) / [VALIDATION_V2.md](VALIDATION_V2.md) |

v2의 구조 변경 (2025 설계 구간에서만 결정, 2026 홀드아웃 분리):
프로브-피라미드 폐지(풀 엔트리), +1R 부분익절 제거(본전 이동은 유지),
챈들리어 3.25, P2/P3 비활성. P1의 CHOP 확장안은 설계 구간 +40%에도
홀드아웃 −4.7%로 **기각** — 상세는 [REDESIGN_V2.md](REDESIGN_V2.md).

**리스크 스케일링 프론티어** (v2 전 기간 실측): r_base 0.75% → +8.4%/−6.8%,
1.00% → +11.2%/−8.9%, 1.25% → +14.2%/−10.9%, **1.50% → 하드스톱 충돌로 수익 붕괴**.
기본값은 0.75%를 유지하고, 페이퍼 전방 기대값 확인 후에만 1.25%까지 상향 검토.

주의: 홀드아웃(2026 상반기)은 v2도 −0.8%(트레이드 기대값은 +0.047R로 양전,
v1 −1.4%/−0.039R 대비 개선)다. 2026 상반기는 추세 부재 구간으로 추세추종의
정직한 한계이며, 그래서 실탄 전 페이퍼 전방 검증이 필수다. 게이트는
"지지 않는 시스템"을 보증할 뿐 수익률을 보증하지 않는다.

## 구조

```
fabletradebot/
  config.py         # 청사진의 모든 파라미터 (단일 출처)
  preprocess.py     # N/A 결측 행 무조건 배제 등 전처리
  indicators.py     # ER, OLS t-stat, EWMA vol, ATR, BBW, Donchian 등 순수 함수
  regime.py         # Market Regime Engine (TREND/SQUEEZE/CHOP/CRISIS + 히스테리시스)
  signals.py        # Alpha Signal Logic: 증거벡터(E1~E5) + 플레이북 P1~P4 + θ(regime)
  risk.py           # 분수 켈리 사이징, 국면/성과/유동성 승수, 포트폴리오 캡, 서킷 브레이커
  engine.py         # Probe-and-Pyramid 포지션 수명주기, 청산 로직, EV 게이트, 쿨다운
  backtest.py       # 이벤트 기반 백테스터 (수수료 + 슬리피지 + 펀딩 반영)
  synthetic.py      # 국면 순환 합성 데이터 생성기 (로직 검증용)
  data_okx.py       # OKX 퍼블릭 API 수집기 (1H 캔들 + 8h 펀딩, CSV 캐시/증분 갱신)
  journal_notion.py # Notion 매매일지 (NOTION_TOKEN 설정 시에만 활성)
  okx_exec.py       # 라이브 주문 어댑터 (LIVE_CONFIRM=YES까지 4중 안전장치, 기본 dry-run)
validation.py       # BLUEPRINT §6 검증 게이트 4종 실행 → VALIDATION.md 생성
run_live.py         # 시간당 페이퍼/라이브 루프 (결정론적 리플레이 설계)
.github/workflows/paper-trade.yml  # 매시 7분 자동 실행 + 저널 커밋
```

## 페이퍼 / 라이브 운영

```bash
python3 validation.py                # 실데이터 검증 게이트 (데이터 자동 다운로드)
TRADE_MODE=paper python3 run_live.py # 페이퍼 트레이딩 1스텝 (기본값)
```

- **페이퍼 트레이딩(기본)**: 고정 앵커일부터 전체 리플레이 → 신규 트레이드만
  `journal/paper_trades.csv`에 추가 기록. 상태 파일이 꼬일 수 없는 결정론적 설계.
- **Notion 일지**: GitHub Secrets에 `NOTION_TOKEN`, `NOTION_DATABASE_ID` 설정 시 자동 기록.
  (DB 속성: Name/Asset/Playbook/Direction/R/PnL/Reason/Closed)
- **라이브 전환**: `TRADE_MODE=live` + OKX API 키 3종 + `LIVE_CONFIRM=YES`가 전부
  설정되어야만 실제 주문. 하나라도 빠지면 dry-run 출력만 한다.
  실주문 전 반드시 OKX 데모 트레이딩(`OKX_DEMO=1`)으로 먼저 검증할 것.
- **주의**: OKX 펀딩레이트 히스토리는 최근 3개월만 제공되므로, 그 이전 구간은
  E3 증거가 중립 처리되고 P4가 비활성화된다.

## 호라이즌 매핑 (데이 / 스윙)

| 플레이북 | 국면 | 호라이즌 | 최대 보유 | 시간 손절 |
|---|---|---|---|---|
| P1 Squeeze Breakout | SQUEEZE | 스윙 | 240봉 (~10일) | 12봉 내 MFE < 0.5R |
| P2 Trend Pullback | TREND | 스윙 | 240봉 | 12봉 |
| P3 Sweep Reversal | CHOP | 데이 | 24봉 (24h) | 8봉 |
| P4 Funding Squeeze | 전천후 | 데이 | 72봉 | 16봉 |

(표는 1H 봉 수 기준. 운영 채택된 4H 템포에서는 `config.h4_config()`가 시간 단위
파라미터를 실시간 기준으로 환산한다 — 예: P1 최대 보유 90봉=15일, P3 6봉=24h.)

## 실행

```bash
pip install pandas numpy pytest
python3 -m pytest tests/ -q      # 로직 검증 (24 tests)
python3 run_backtest.py 6000 7   # 합성 데이터 데모 백테스트 (bars, seed)
```

## 실데이터 사용법

`Backtester`는 자산별 1H OHLCV DataFrame(컬럼: open/high/low/close/volume,
DatetimeIndex)과 선택적 8h 펀딩레이트 Series를 받는다:

```python
from fabletradebot import Backtester, Config
res = Backtester({"BTC": btc_df, ...}, Config(), funding={"BTC": btc_funding}).run()
print(res.summary())
```

오더북 심도/테이커 델타가 없으면 E2 증거는 캔들 프록시로 대체되고
유동성 참여 캡은 비활성화된다 (라이브에서는 오더북 스냅샷으로 활성화).

## 합성 데이터 검증 결과 해석 시 주의

합성 데이터의 추세 구간에는 진짜 엣지(드리프트)가 있으므로 P1/P2가 작동하는지 확인할 수
있지만, P3(스윕 리버설)의 엣지는 실제 시장의 강제 청산 흐름에서 나오므로 무작위 합성
데이터에서는 원리적으로 나타나지 않는다. 합성 백테스트의 목적은 **수익 추정이 아니라
메커니즘 검증**(리스크 통제, 회계 정합성, 국면 전환 대응)이다. 수익성 검증은 Phase 3에서
실데이터 walk-forward로 수행한다 (BLUEPRINT.md §6 게이트).
