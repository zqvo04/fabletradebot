# FableTradeBot

BTC/ETH/SOL/HYPE 무기한 선물 대상 국면 적응형(regime-adaptive) 데이+스윙 트레이딩 시스템.
설계 근거와 수식은 [BLUEPRINT.md](BLUEPRINT.md) 참조. **현재 Phase 2: 순수 매매 로직 + 백테스트만 구현.**
거래소 API 연동, 스케줄링, 매매일지, 페이퍼 트레이딩 스위치 등 인프라는 Phase 3에서 추가한다.

## 구조

```
fabletradebot/
  config.py      # 청사진의 모든 파라미터 (단일 출처)
  preprocess.py  # N/A 결측 행 무조건 배제 등 전처리
  indicators.py  # ER, OLS t-stat, EWMA vol, ATR, BBW, Donchian 등 순수 함수
  regime.py      # Market Regime Engine (TREND/SQUEEZE/CHOP/CRISIS + 히스테리시스)
  signals.py     # Alpha Signal Logic: 증거벡터(E1~E5) + 플레이북 P1~P4 + θ(regime)
  risk.py        # 분수 켈리 사이징, 국면/성과/유동성 승수, 포트폴리오 캡, 서킷 브레이커
  engine.py      # Probe-and-Pyramid 포지션 수명주기, 청산 로직, EV 게이트, 쿨다운
  backtest.py    # 이벤트 기반 백테스터 (수수료 + 슬리피지 + 펀딩 반영)
  synthetic.py   # 국면 순환 합성 데이터 생성기 (로직 검증용)
```

## 호라이즌 매핑 (데이 / 스윙)

| 플레이북 | 국면 | 호라이즌 | 최대 보유 | 시간 손절 |
|---|---|---|---|---|
| P1 Squeeze Breakout | SQUEEZE | 스윙 | 240봉 (~10일) | 12봉 내 MFE < 0.5R |
| P2 Trend Pullback | TREND | 스윙 | 240봉 | 12봉 |
| P3 Sweep Reversal | CHOP | 데이 | 24봉 (24h) | 8봉 |
| P4 Funding Squeeze | 전천후 | 데이 | 72봉 | 16봉 |

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
