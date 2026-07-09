# FableTradeBot — Quant Algorithm Blueprint (v0.1, Logic-Only)

> Phase 1 산출물: 수학적 수식 + 의사코드 + 설계도.
> 인프라(OKX API, 스케줄러, Notion, Paper trading 스위치)는 Phase 2에서 다룬다.
> 전처리 대전제: **입력 데이터에 N/A/결측치가 있는 행은 무조건 배제(drop)** 후 모든 추정치를 계산한다.

---

## 0. 투자 철학 — 엣지는 어디서 오는가

50%/월은 "예측을 더 잘하는 것"으로는 달성 불가능하다. 예측 정확도는 아무리 올려도 55~60%가 한계다.
달성 가능한 유일한 경로는 **손익의 기하학(geometry)을 비대칭으로 설계**하는 것이다:

1. **강제 청산 흐름(Forced Flow) 사냥** — 크립토 무기한 선물 시장의 손실은 대부분 "의견"이 아니라 "강제된 행동"(청산, 스탑 캐스케이드, 펀딩 압박에 의한 포지션 언와인드)에서 나온다. 강제 매도/매수는 가격에 무관하게 발생하므로, 그 반대편에 서는 것이 구조적 엣지다.
2. **변동성 군집(Vol Clustering)** — 저변동성 압축은 고변동성 팽창을 예고한다. 추세의 '탄생 지점'(squeeze breakout)에서만 공격적으로 진입하면, 손절은 좁고 이익은 열려 있다.
3. **횡단면 선행-후행(Lead-Lag)** — BTC가 조류(tide)를 만들고 SOL/HYPE가 증폭한다. BTC 국면을 매크로 게이트로 쓰면 알트 매매의 노이즈가 급감한다.
4. **볼록성(Convexity)** — 대부분의 달은 소폭 그라인딩하고, 한 달에 2~3번 잡히는 진짜 추세에서 피라미딩으로 8~15R을 뽑는다. 50% 목표는 "매일 1.4%"가 아니라 "우측 꼬리(fat right tail)를 놓치지 않는 설계"로 접근한다.

**CIO의 정직한 한 줄**: 매달 50%를 보장하는 알고리즘은 존재하지 않는다. 설계 가능한 것은 (a) 파산 확률을 구조적으로 0에 가깝게 억제하면서 (b) 기하평균 성장률을 최대화하고 (c) 추세 달에 수익이 폭발하도록 볼록성을 심는 것이다. 이 청사진은 그 세 가지를 목표로 한다.

---

## 1. Market Regime Engine — 시장 국면 판단

### 1.1 입력 및 전처리

- 기본 타임프레임: **1H 봉** (신호), 4H/1D (컨텍스트).
- 자산별 시계열: OHLCV, funding rate(8h), orderbook snapshot(±0.5% 심도), (가능 시) taker buy/sell volume.
- 전처리 파이프라인:
  ```
  1. N/A, null, 0-volume 이상치 행 → drop
  2. 수익률 r_t = ln(C_t / C_{t-1})
  3. 추정용 수익률은 ±5σ에서 winsorize (극단 이상치가 추정치를 오염시키는 것 방지)
  4. 최소 히스토리 미달(< 30일) 자산 → 매매 대상 제외
  ```

### 1.2 국면 특징량 (Feature Set)

**(a) 추세 강도 — Kaufman Efficiency Ratio (ER)**

```
ER_n = |P_t − P_{t−n}| / Σ_{i=t−n+1..t} |P_i − P_{i−1}|,   n = 24 (1H 기준 24시간)
```
ER ∈ [0,1]. 1에 가까울수록 일방향 이동(추세), 0에 가까울수록 노이즈(횡보).

**(b) 추세 방향 및 유의성 — 회귀 기울기 t-통계량**

```
log P를 최근 48봉에 OLS 회귀 → 기울기 β, 표준오차 se(β)
T_stat = β / se(β)
D = sign(T_stat),  추세 유의 조건: |T_stat| ≥ 2
```

**(c) 변동성 상태 — EWMA 변동성의 백분위**

```
σ²_t = λ·σ²_{t−1} + (1−λ)·r²_t,   λ = 0.94
V_pct = PercentileRank(σ_t, 최근 30일 분포)
```

**(d) 압축 감지 — Bollinger Band Width 백분위**

```
BBW = (Upper − Lower) / Middle,   (20, 2σ)
Squeeze 조건: PercentileRank(BBW, 90일) ≤ 15%
```

**(e) 포지셔닝 온도 — Funding z-score**

```
F_z = (funding_t − μ_funding,30d) / σ_funding,30d
```
|F_z| ≥ 2 → 한쪽 포지셔닝 과밀 = 언와인드(스퀴즈) 연료.

### 1.3 국면 분류 (자산별, 매 봉)

```
CRISIS    : V_pct ≥ 85  AND (최근 1H 수익률 < −4σ  OR  vol-of-vol 급등)
TREND_UP  : ER ≥ 0.35 AND |T_stat| ≥ 2 AND D > 0
TREND_DOWN: ER ≥ 0.35 AND |T_stat| ≥ 2 AND D < 0
SQUEEZE   : ER < 0.35 AND BBW_pct ≤ 15        ← 추세 '탄생 대기' 상태
CHOP      : 그 외 전부
```

**히스테리시스(whipsaw 방어)**: 국면 전환은 새 국면이 **3봉 연속** 유지될 때만 확정.
단, → CRISIS 전환만은 **즉시 적용**(방어는 지연 없이, 공격은 확인 후).

### 1.4 매크로 게이트 (BTC = 조류)

```
IF BTC_regime == CRISIS:
    모든 알트(SOL, HYPE) 포지션 즉시 청산, 신규 알트 진입 금지
    BTC/ETH만 P3(스윕 리버설)·P4(펀딩 스퀴즈) 소형 사이즈 허용
IF 알트 신규 진입:
    BTC 4H 방향과 역행하는 알트 추세추종 진입 금지 (역추세 P3/P4는 예외)
```

---

## 2. Alpha Signal Logic — 진입/청산 및 FP/FN 최적화

### 2.1 증거 벡터 (Evidence Vector)

모든 후보 트레이드(자산, 방향)에 대해 서로 **직교(orthogonal)한** 5개 증거를 [0,1]로 산출:

| 증거 | 내용 | 측정 |
|------|------|------|
| E1 구조 | 가격 구조 이벤트 품질 | Donchian(48) 돌파의 종가 확정 여부 + 봉 range 확장(> 1.5×ATR) / SFP 꼬리 품질 |
| E2 오더플로우 | 실제 수급 방향 | 테이커 델타(매수량−매도량) 부호 일치 + OBI = (ΣBidDepth − ΣAskDepth)/(Σ전체), ±0.5% 심도 |
| E3 포지셔닝 | 펀딩이 트레이드에 유리한가 | 진입 방향 역풍 펀딩(F_z가 같은 방향 ≥ +2)이면 0, 순풍이면 1, 중립 0.5 |
| E4 횡단면 | BTC 확인 | (알트만) BTC 최근 4H 수익률 방향 일치 + BTC 국면 정렬 |
| E5 변동성 맥락 | 압축→팽창 국면인가 | BBW 백분위가 저점에서 상승 전환 중이면 가점 |

**종합 확신도**:
```
Z = Σ w_i(regime) · E_i,   Z ∈ [0,1]
```
가중치 w는 국면별로 다르다 (예: TREND에서는 E1·E4↑, CHOP에서는 E2·E3↑).

### 2.2 플레이북 (국면 → 셋업 매핑)

**P1. Squeeze Breakout (국면: SQUEEZE → 추세 탄생 포착) — 주 수익 엔진**
```
셋업: BBW_pct ≤ 15 상태에서
진입: 종가가 Donchian(48) 상단/하단 돌파 + 거래량 ≥ 1.5×평균 + E2 방향 일치
손절: 압축 박스 반대편 or 진입가 ∓ 1.5×ATR (더 가까운 쪽)
목표: 없음(트레일링) — 추세 탄생이므로 이익은 열어둔다
기대 R:R ≈ 1 : 3~8
```

**P2. Trend Pullback (국면: TREND) — 추세 재승차**
```
셋업: 추세 방향 유지 중 EMA(20)~AVWAP 존까지 되돌림 + F_z 과열 해소(< +1)
진입: 재개봉(resumption bar: 종가가 직전 봉 고가/저가 돌파) 확정 시
손절: 되돌림 극점 ∓ 0.5×ATR
기대 R:R ≈ 1 : 2.5~4
```

**P3. Liquidity Sweep Reversal (국면: CHOP) — 스탑헌팅 역이용**
```
셋업: 레인지 극단에서 SFP(Swing Failure Pattern):
      직전 스윙 고/저를 꼬리로 이탈 → 같은 봉 or 다음 봉 종가가 레인지 내부 복귀
확인: 이탈 순간 테이커 델타와 가격의 다이버전스 + 갇힌 방향의 F_z 과밀
진입: 복귀 확정 종가
손절: 스윕 꼬리 극점 밖
목표: 레인지 중앙(50% 청산) → 반대편 극단
기대 R:R ≈ 1 : 1.5~2.5, 고승률
```

**P4. Funding Squeeze (전천후, 주로 필터 + 가끔 단독)**
```
셋업: |F_z| ≥ 2 AND 최근 12H 동안 가격이 펀딩 방향으로 전진 실패(진행률 < 0.5×ATR)
     → 과밀 포지션의 언와인드 베팅 (예: 펀딩 +2σ인데 가격 정체 → 롱 과밀 → 숏)
사이즈: 표준의 50%, 손절 넓게(2×ATR), 목표 비대칭(3R+)
```

### 2.3 [핵심 딜레마] FP/FN 트레이드오프의 구조적 해법

**원칙: 오류의 '개수'가 아니라 오류의 '비용'을 최적화한다.**
- False Positive 비용 = 손절폭 × 사이즈 → **내가 통제 가능**
- False Negative 비용 = 놓친 추세 → **재진입 로직으로 통제 가능**
- 따라서 분류 정확도를 올리는 대신, 두 오류의 비용을 국면별로 재설계한다.

**(1) 국면 조건부 임계값 θ(regime)**

기대값이 양수일 때만 진입:
```
EV(θ) = p(θ)·W(regime) − (1−p(θ))·L − cost(fee+slip+funding) > 0
진입 조건: Z ≥ θ(regime)
```
| 국면 | θ | 논리 |
|------|-----|------|
| SQUEEZE / TREND | **낮음 (0.55)** | FN이 치명적(추세 놓침 = 그 달의 수익 엔진 상실). FP는 타이트한 손절로 비용 상한이 낮다. W/L ≈ 3~8이므로 승률 30%대도 EV+ |
| CHOP | **높음 (0.75)** | FP가 치명적(횡보장 천 번의 손절 = death by a thousand cuts). FN은 값싸다 — 레인지는 기회를 반복 제공한다 |
| CRISIS | **∞ (진입 불가)** | 어떤 신호도 신뢰하지 않는다 |

**(2) Probe-and-Pyramid: 분류 오류를 사이징 오류로 변환**

이진(진입/미진입) 결정을 연속 변수로 바꾸는 것이 수학적 핵심이다:
```
1단(Probe)  : Z ≥ θ_probe   → 전체 리스크 유닛의 1/3 진입
2단(Confirm): 가격이 +0.5×ATR 전진 AND 리테스트 유지 → 1/3 추가, 손절 유지
3단(Runner) : (TREND/SQUEEZE 국면 한정) 다음 구조 돌파 시 1/3 추가
              + 전체 손절 → 본전(breakeven) 상향
```
효과: 가짜 신호(FP)의 실현 손실 ≈ 0.33R (풀사이즈의 1/3), 진짜 추세(FN 방지)는 자동으로 풀사이즈 도달.
→ **오분류 1회의 비용이 비대칭이 된다: FP ≈ −0.33R vs 추세 포착 ≈ +8R.**

**(3) 재진입 규칙 (FN 2차 방어선)**

```
손절 후 동일 방향 신호가 더 높은 Z로 재점화 → 최대 2회 재진입 허용
재진입 사이즈 = 직전 × 0.7 (FP 연쇄 비용 기하급수 감쇠)
3회 손절 → 해당 자산 24H 쿨다운
```

**(4) 시간 손절 (가격 손절이 못 잡는 slow-bleed FP 제거)**

```
진입 후 N봉(P1/P2: 12봉, P3: 8봉) 내 MFE < +0.5R → 시장가 청산
논리: 좋은 트레이드는 빨리 작동한다. 작동하지 않는 것 자체가 정보다.
```

### 2.4 청산 로직

```
1. 부분 익절: +1R 도달 시 40% 청산, 손절 → 본전
   (FP 리스크의 '보험료'를 시장이 지불하게 만드는 장치)
2. 트레일링 (TREND/P1): Chandelier Stop = max(High, since entry) − 2.75×ATR
3. 고정 목표 (CHOP/P3): 레인지 반대편 극단
4. 국면 붕괴 청산: 보유 중 국면이 포지션에 적대적으로 전환(예: TREND_UP → CRISIS)
   → 전량 즉시 청산 (신호보다 국면이 상위 권한)
```

---

## 3. Risk & Sizing Model — 파산 방지와 기하 성장 극대화

### 3.1 사이징의 뼈대: 변동성 정규화 + 분수 Kelly

**켈리 기준과 왜 1/4만 쓰는가:**
```
f* = (p·b − q) / b        (p: 승률, q=1−p, b: 평균 손익비)
블렌디드 가정 p ≈ 0.45, b ≈ 2.2  →  f* ≈ 0.20 (자본의 20%)
채택: k = 0.25 (quarter-Kelly) → 트레이드당 리스크 기본값 r_base = 0.75%
```
이유: p, b는 **추정치**이고 추정 오차 하에서 풀켈리는 파산 가속기다.
기하 성장률은 f*에서 최대지만, f* 초과 시 성장률이 음수로 붕괴하는 비대칭 — 왼쪽(과소배팅)으로 틀리는 것이 항상 옳다.

**트레이드당 리스크 (동적 조절):**
```
r_eff = r_base × m_regime × m_perf × m_liquidity

m_regime   : TREND 1.25 / SQUEEZE 1.0 / CHOP 0.6 / CRISIS 0
m_perf     : 최근 20트레이드 누적 R 기준 (equity-curve throttle)
             < −3R → 0.5  |  −3R~+5R → 1.0  |  > +5R → 1.25 (상한 1.5)
             ← 안티마틴게일: 시스템이 시장과 동조 중일 때만 공격
m_liquidity: BTC/ETH 1.0 / SOL 0.8 / HYPE 0.5
```

**포지션 수량 (변동성/손절 정규화 — 자산별 템포 자동 적응의 핵심):**
```
Q = (r_eff × Equity) / |Entry − Stop|
```
손절폭이 ATR 기반이므로, HYPE처럼 변동성이 큰 자산은 **자동으로 수량이 작아진다.**
레버리지는 결과값일 뿐이며 상한만 둔다: BTC/ETH ≤ 5× / SOL ≤ 3× / HYPE ≤ 1.5×.

### 3.2 유동성 참여 제약 (HYPE 대응)

```
Notional ≤ 5% × (±0.5% 심도 내 가시 유동성)
제약에 걸리면: 손절을 넓히지 말고 사이즈를 깎는다.
호가 스프레드 > 0.15% → 시장가 진입 금지, 지정가만 (미체결 시 포기 = FN 수용)
```
저유동성 자산에서 FN(기회 포기)은 싸고 슬리피지(확정 비용)는 비싸다 — CHOP와 같은 논리.

### 3.3 포트폴리오 레벨 제약 (크립토 = 단일 베타 인정)

```
β 가중 순방향 노출: Σ |Notional_i × β_i| ≤ 2.0 × Equity
                    (β: BTC 1.0, ETH 1.1, SOL 1.5, HYPE 2.0)
동시 포지션 ≤ 4개, 동일 방향 상관 포지션 ≤ 3개
```

### 3.4 서킷 브레이커 (Ruin 방지 — 협상 불가 규칙)

```
일일 손실 −3%  → 전량 청산 + 24H 매매 정지
주간 손실 −7%  → 다음 주 r_base 50% 감축
최대 낙폭 −15% (HWM 대비) → 시스템 전면 정지, 파라미터 재검증 전 재가동 금지
```

### 3.5 캐리 회계

```
진입 전 EV에서 예상 보유기간 × 펀딩 비용을 차감.
펀딩이 포지션 방향으로 지급되면 Z에 소폭 가점 (E3에 이미 반영).
```

---

## 4. 통합 의사코드 (Main Loop)

```
EVERY 1H bar close, FOR each asset in [BTC, ETH, SOL, HYPE]:

  # 0. 전처리
  data ← load(OHLCV, funding, orderbook)
  data ← drop_na(data); data ← winsorize(returns, 5σ)
  if history < 30d: skip asset

  # 1. 국면
  regime[asset] ← classify(ER, T_stat, V_pct, BBW_pct)   # + 3봉 히스테리시스
  if regime[BTC] == CRISIS:
      close_all_alt_positions(); alt_entry_blocked ← True

  # 2. 보유 포지션 관리 (신규 진입보다 먼저)
  for pos in open_positions[asset]:
      if regime_hostile(pos, regime): close(pos); continue
      if MFE(pos) ≥ 1R and not pos.scaled: take_profit(40%); stop → breakeven
      update_trailing_or_target(pos, regime)
      if bars_held ≥ N and MFE < 0.5R: close(pos)          # 시간 손절
      if pyramid_trigger(pos, Z, regime): add_unit(pos)     # 2단/3단

  # 3. 신규 신호
  playbooks ← select_playbooks(regime)          # P1~P4
  for pb in playbooks:
      E ← evidence_vector(pb, data, btc_context)
      Z ← Σ w_i(regime)·E_i
      if Z ≥ θ(regime) and portfolio_constraints_ok() and circuit_breakers_ok():
          stop ← structural_stop(pb)
          if EV(p̂, W, L, fees, funding) ≤ 0: skip
          Q ← (r_eff × Equity) / |entry − stop|
          Q ← min(Q, liquidity_cap, leverage_cap)
          open_probe(asset, direction, Q/3, stop)           # 1/3 프로브

  # 4. 회계
  update_equity_curve(); update_m_perf(); check_circuit_breakers()
```

---

## 5. 기대 수익 산술 (목표의 정직한 분해)

```
가정: 4자산 × 1H 시스템 → 월 유효 트레이드 약 50~70건
  CHOP/P3 계열: 승률 ~55%, 평균 +1.6R / −1R      → 기대치 ≈ +0.43R/건
  TREND/P1 계열: 승률 ~35%, 평균 +4.5R / −0.45R  → 기대치 ≈ +1.28R/건
                 (probe 구조로 평균 손실이 −1R보다 작음)
r_eff ≈ 0.6~0.95% → 그라인딩 달: +8~15%
추세 달: P1 피라미드 2~3회 성공 시 +25~40R 추가 → 50%+ 도달 가능
```
즉 50%는 "평균"이 아니라 "추세 달의 상단"이며, 시스템의 역할은
(a) 그라인딩 달에 죽지 않고 (b) 추세 달에 반드시 배에 타 있는 것이다.

---

## 6. Phase 2 승인 전 검증 기준 (합의용)

로직 승인 후 구현 단계에서 아래를 통과해야 실탄 투입 논의 가능:
1. **Walk-forward 백테스트**: in-sample 최적화 금지, 파라미터는 위 명세 고정값으로 시작
2. **파라미터 민감도**: θ, ER 임계, ATR 배수를 ±20% 흔들어도 파산하지 않을 것
3. **비용 스트레스**: 수수료 2×, 슬리피지 2× 가정에서도 EV > 0 유지
4. **몬테카를로 시퀀스 셔플**: 최대 낙폭 분포의 95퍼센타일이 −25% 이내
