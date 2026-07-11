# fabletradebot V1 — discrete 방향성 레버리지 진입 엔진

고확신 방향성 셋업을 1H 마감마다 코인별 독립 스캔해, 자격 있는 순간에만
확신도 티어별 레버리지(2/3/5/10x)로 discrete 진입하고, 손절이 청산보다 항상
먼저 맞도록 구조적으로 봉쇄한 위에서 기하수익을 최대화한다.

- 설계: [BLUEPRINT.md](BLUEPRINT.md) (수식·파라미터·게이트)
- 실험 로그(채택/기각 전부): [EXPERIMENTS.md](EXPERIMENTS.md)
- 검증 결과: [VALIDATION.md](VALIDATION.md)
- 시크릿: [SECRETS.md](SECRETS.md) · 스케줄러: [CRON_SETUP.md](CRON_SETUP.md)

## 구조

```
fabletradebot/
  config.py        유니버스·비용·전 파라미터 (단일 출처)
  data_okx.py      OKX 1H 캔들+펀딩, CSV 캐시+증분, 무룩어헤드 투영
  indicators.py    순수 지표 함수
  regime.py        BTC 1D 광역 국면(4상태+히스테리시스), 상관 경보
  signals.py       S1 되돌림 / S2 스퀴즈 돌파 / S3 스윕 반전 / S4 펀딩 수정자
  risk.py          확신도→티어, 청산안전 캡, 고정 리스크 사이징
  engine.py        결정론적 마감봉 리플레이 엔진 (백테스트=페이퍼 동일 코드)
  backtest.py      오케스트레이션+지표
  validation.py    워크포워드·민감도·비용 스트레스·몬테카를로
  scoring.py       채점(진단, 매매 로직과 분리)
  notify.py        Telegram
  journal_notion.py Notion 저널 (v5 DB 재사용)
  okx_exec.py      라이브 스켈레톤 (4중 잠금, V1은 페이퍼 전용)
run_live.py        1H 페이퍼 스텝 (앵커부터 결정론적 리플레이)
run_backtest.py    CLI 백테스트
fetch_data.py      데이터 백필/갱신
```

## 실행

```bash
pip install pandas numpy pytest
python3 -m pytest tests/ -q          # G1 메커니즘 게이트
python3 fetch_data.py data           # 히스토리 백필
python3 run_backtest.py 2023-06-01 2026-01-31 data
TRADE_MODE=paper python3 run_live.py # 1회 페이퍼 스텝
```

## 안전 규칙 (요약)

- 트레이드당 손실 = 계좌의 0.5~1.5% 고정(확신도 티어). 청산가 거리 ≥ 3×손절 거리.
- 동시 ≤4 포지션, 총 오픈 리스크 ≤4.5%, DD 거버너, 24h 서킷 브레이커, CRISIS 진입 금지.
- 성패 판정은 백테스트가 아니라 **페이퍼 전방 4주**(G8) — 통과 전 라이브 금지.
