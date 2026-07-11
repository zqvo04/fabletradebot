# SECRETS — 시크릿 키 설정 가이드

봇의 선택적 연동(Notion 신호 로그 · Telegram 알림 · OKX 실계정 데이터/주문)은
모두 **환경변수(시크릿)** 로만 켜진다. 하나도 설정하지 않으면 봇은 그대로 페이퍼
트레이딩만 하고, 각 연동은 조용히 비활성(no-op)된다. 실패해도 트레이드 루프는
절대 멈추지 않도록 설계돼 있다.

> ⚠️ **어떤 키도 코드/커밋/이슈에 절대 붙여넣지 말 것.** 아래 값들은 전부
> GitHub Actions Secrets 또는 로컬 환경변수로만 넣는다. 키가 유출되면 즉시
> 해당 서비스에서 폐기(revoke)하고 재발급한다.

---

## 넣는 위치

**GitHub Actions (자동 페이퍼 트레이딩):**
저장소 → Settings → Secrets and variables → Actions → **New repository secret**.
아래 표의 이름 그대로 등록하면 `.github/workflows/paper-trade.yml`가 자동으로 읽는다.

**로컬 실행:**
```bash
export NOTION_TOKEN="secret_..."
export NOTION_SIGNAL_DB_ID="d92d056eadeb44228c99094509e94685"
export TELEGRAM_BOT_TOKEN="123456:ABC-..."
export TELEGRAM_CHAT_ID="123456789"
TRADE_MODE=paper python3 run_live_v3.py
```

| 시크릿 이름 | 용도 | 필수? |
|---|---|---|
| `NOTION_TOKEN` | Notion 통합(integration) 토큰 | 신호 로그·매매일지에 필요 |
| `NOTION_SIGNAL_DB_ID` | 신호 로그 DB (아래 생성됨) | 신호 로그에 필요 |
| `NOTION_DATABASE_ID` | 기존 매매일지 DB (v2 트레이드) | v2 일지에만 |
| `TELEGRAM_BOT_TOKEN` | 텔레그램 봇 토큰 | 알림에 필요 |
| `TELEGRAM_CHAT_ID` | 알림 받을 채팅 ID | 알림에 필요 |
| `OKX_API_KEY` / `OKX_API_SECRET` / `OKX_PASSPHRASE` | OKX 3종 키 | 실계정 데이터/주문에만 |
| `SIGNAL_NOTIFY_THRESHOLD` | 신호 발화 임계(비중 변화, 기본 0.03) | 선택 |
| `LIVE_CONFIRM` / `OKX_DEMO` | 실주문 4중 안전장치 | 실거래 전환 시에만 |

---

## 1. Notion — 신호 로그

신호 로그 데이터베이스는 이미 생성되어 있다:

- **DB 이름**: `FableTradeBot — Signal Log`
- **DB URL**: https://app.notion.com/p/d92d056eadeb44228c99094509e94685
- **`NOTION_SIGNAL_DB_ID`**: `d92d056eadeb44228c99094509e94685`

봇이 REST API로 이 DB에 쓰려면 **통합 토큰**을 만들고, 그 통합을 DB에 **연결**해야
한다 (MCP로 만든 페이지는 사용자 OAuth 소유라, 봇용 통합에 별도 권한을 줘야 함):

1. https://www.notion.so/my-integrations → **New integration** 생성
   (이름 예: `fabletradebot`, 권한: Insert content). → **Internal Integration Secret**
   복사 → 이 값이 `NOTION_TOKEN` (`secret_...` 또는 `ntn_...`으로 시작).
2. 위 Signal Log DB 페이지 열기 → 우측 상단 **···** → **Connections**(연결) →
   방금 만든 `fabletradebot` 통합 추가. **이 단계를 빼먹으면 401/404가 난다.**
3. GitHub Secrets에 `NOTION_TOKEN`, `NOTION_SIGNAL_DB_ID` 등록.

> DB ID는 URL의 `?v=` 앞 32자리 16진수다. 다른 DB를 새로 쓰고 싶으면 그 ID로
> `NOTION_SIGNAL_DB_ID`를 바꾸되, 스키마(속성 이름)는 아래와 같아야 한다:
> `Name`(title) · `Bar Time`/`Closed`(date) ·
> `System`/`Asset`/`Direction`/`Status`(select) ·
> `Entry`/`TP`/`SL`/`Exit`/`Result R`/`Target Weight`/`Equity`(number).
> `Status` 옵션: `Open`/`Win`/`Loss`/`Timeout-Win`/`Timeout-Loss`.
> 신호 발화 시 Open 행이 생기고, TP/SL/타임아웃 판정 시 같은 행이 갱신된다.

---

## 2. Telegram — 신호 알림

1. 텔레그램에서 **@BotFather** 대화 → `/newbot` → 안내대로 이름 지정 →
   받은 토큰이 `TELEGRAM_BOT_TOKEN` (`123456:ABC-DEF...` 형식).
2. 방금 만든 봇과 대화를 시작하고 아무 메시지나 한 번 보낸다(그래야 봇이 나에게
   메시지를 보낼 수 있음).
3. 내 chat id 확인: 브라우저에서
   `https://api.telegram.org/bot<봇토큰>/getUpdates` 열기 →
   `"chat":{"id":123456789}` 의 숫자가 `TELEGRAM_CHAT_ID`.
   (그룹/채널로 받으려면 봇을 그 방에 넣고 같은 방법으로 음수 chat id 확인.)
4. GitHub Secrets에 `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID` 등록.

발화 시 이런 메시지가 온다:
```
⚡ v3 신호 — SOL 🟢 LONG
목표 비중 +0.187 (이전 +0.021, Δ +0.166)
자본 $120,004  ·  봉 2026-07-10 08:00:00+00:00
```

---

## 3. OKX — 3종 키 (데이터/주문)

OKX → 우측 상단 프로필 → **API** → **Create API Key**. 세 값이 나온다:
`OKX_API_KEY` · `OKX_API_SECRET` · `OKX_PASSPHRASE`(생성 시 직접 정하는 문구).

- **권한 설정**: 실계정 잔고/포지션 **조회만** 쓸 거면 **Read** 권한만 부여
  (가장 안전). 나중에 실주문까지 하려면 **Trade** 권한과 IP 화이트리스트 추가.
- **읽기(잔고 조회)** 는 3종 키만 있으면 켜진다 —
  `run_live_v3.py`가 실계정 equity를 가져와 알림/상태에 반영한다.
  키가 없으면 자동으로 페이퍼 equity로 폴백한다.
- **실주문은 4중 안전장치**를 모두 만족해야만 나간다:
  `TRADE_MODE=live` + 3종 키 + `LIVE_CONFIRM=YES`. 하나라도 빠지면 주문은
  실제로 전송되지 않고 `DRY RUN`으로 출력만 된다.
- **반드시 데모부터**: 실주문 전 `OKX_DEMO=1`로 OKX 데모 트레이딩에서 먼저 검증.
  (요청 헤더에 `x-simulated-trading: 1`이 붙어 모의 환경으로 간다.)

```bash
# 실계정 잔고 읽기만 (안전) — 페이퍼 트레이딩에 실 equity만 반영
export OKX_API_KEY=... OKX_API_SECRET=... OKX_PASSPHRASE=...
TRADE_MODE=paper python3 run_live_v3.py

# 데모 실주문 검증
OKX_DEMO=1 TRADE_MODE=live LIVE_CONFIRM=YES python3 run_live_v3.py
```

---

## 보안 체크리스트

- [ ] 키는 GitHub Secrets / 로컬 env에만. **커밋·로그·스크린샷 금지.**
- [ ] OKX 키는 처음엔 **Read 전용**, 실거래 시에만 Trade + **IP 화이트리스트**.
- [ ] 실주문은 데모(`OKX_DEMO=1`)로 먼저, 그 다음 소액으로.
- [ ] 유출 의심 시 즉시 각 서비스에서 **폐기 후 재발급**.
- [ ] `journal/`에 쌓이는 로그에는 키가 들어가지 않는다(목표 비중·자본만 기록).
