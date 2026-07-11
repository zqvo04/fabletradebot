# SECRETS — 시크릿 설정 가이드 (V1)

연동(Notion 저널 · Telegram 알림 · OKX 계정 읽기/실주문)은 전부 **환경변수(시크릿)**
로만 켜진다. 하나도 없으면 봇은 조용히 페이퍼 트레이딩만 하고, 연동 실패가
트레이드 루프를 멈추는 일은 없다.

> ⚠️ 어떤 키도 코드/커밋/이슈에 붙여넣지 말 것. 유출 시 즉시 폐기·재발급.

## 넣는 위치

**GitHub Actions:** 저장소 → Settings → Secrets and variables → Actions →
New repository secret. 아래 이름 그대로 등록하면 `.github/workflows/paper-trade.yml`
이 자동으로 읽는다. (v5에서 쓰던 시크릿 이름과 동일 — 이미 등록돼 있으면 그대로 동작.)

**로컬:**
```bash
export NOTION_TOKEN="secret_..."
export NOTION_SIGNAL_DB_ID="d92d056eadeb44228c99094509e94685"   # 기존 v5 DB 재사용
export TELEGRAM_BOT_TOKEN="123456:ABC-..."
export TELEGRAM_CHAT_ID="123456789"
TRADE_MODE=paper python3 run_live.py
```

| 시크릿 | 용도 | 필요 시점 |
|---|---|---|
| `NOTION_TOKEN` | Notion 통합 토큰 | 저널 기록 |
| `NOTION_SIGNAL_DB_ID` | `FableTradeBot — Signal Log` DB id | 저널 기록 |
| `TELEGRAM_BOT_TOKEN` / `TELEGRAM_CHAT_ID` | 진입/청산 알림 | 알림 |
| `OKX_API_KEY` / `OKX_API_SECRET` / `OKX_PASSPHRASE` | OKX 3종 키 | 실계정 읽기·(향후) 주문 |
| `LIVE_CONFIRM` | `I-UNDERSTAND-LIQUIDATION-RISK` 문구 | 라이브 전환 시에만 |
| `OKX_DEMO` | `1`이면 데모 트레이딩 헤더 | 라이브 전 데모 검증 |
| `LIVE_ANCHOR` | 페이퍼 리플레이 시작일 (기본 2026-07-12) | 선택 |
| `PAPER_EQUITY0` | 페이퍼 초기 자본 (기본 10000) | 선택 |

## Notion DB 스키마 (이미 준비됨)

기존 v5 DB(`FableTradeBot — Signal Log`)를 재사용한다. V1이 쓰는 속성/옵션은
2026-07-11에 DB에 전부 추가돼 있다: System 옵션 `V1`, Asset 옵션
BNB/SUI/WLD/DOGE/LINK/AVAX, 숫자 속성 `Confidence`·`Lev PnL %`·`Hold Hours`.
Notion API는 DB에 없는 속성/옵션이 하나라도 있으면 요청 전체를 400 거부하므로,
새 속성을 코드에 추가할 땐 **반드시 DB에 먼저 만들 것**.

## 라이브 4중 잠금 (V1은 스켈레톤)

`TRADE_MODE=live` + OKX 3키 + `LIVE_CONFIRM` 문구 + 데모 검증(`OKX_DEMO=1`)이
모두 갖춰져도 V1의 `place_order`는 `NotImplementedError`를 던진다. 실주문 배선은
페이퍼 전방 4주 게이트(G8) 통과 후의 별도 변경이다.
