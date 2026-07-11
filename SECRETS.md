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
> `Entry`/`TP`/`SL`/`Exit`/`Result R`/`PnL %`/`Leverage`/`Target Weight`/`Equity`(number).
> `Status` 옵션: `Open`/`Win`/`Loss`/`Timeout-Win`/`Timeout-Loss`.
> `System` 옵션엔 `v5`가 포함된다 (워크플로는 V5만 발화).
> 신호 발화 시 Open 행이 생기고(진입가·TP·SL·목표비중·**Leverage** 기록),
> TP/SL/타임아웃 판정 시 같은 행이 갱신된다(**PnL %**·Result R·Exit·Closed).
> **`Leverage`** = 확신도 계층 계좌 레버리지(2/3/5/10x 하드스톱, 사이징 아님),
> **`PnL %`** = 청산 시 신호의 가격 변화 손익(부호 포함, 예: `+9.24`).
> ⚠️ **정정**: Notion API는 스키마에 없는 속성명이 요청에 섞이면 그 속성만
> 무시하는 게 아니라 **요청 전체를 400으로 거부한다.** 기존 DB를 계속 쓰려면
> `Leverage`(Number)·`PnL %`(Number) 속성을 반드시 추가해야 하고, `System`
> select 속성에 `v5` 옵션도 미리 추가해야 한다(마찬가지로 없는 select 옵션은
> 자동 생성되지 않고 거부될 수 있다). 운영 중인 Signal Log DB는 이미 갱신 완료.

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

### 3-1. `[okx] balance fetch failed: HTTP Error 401` 해결

3종 키가 전부 설정된 상태에서(하나라도 빠지면 이 로그 자체가 안 뜨고 조용히
페이퍼 폴백된다) OKX가 인증을 거부한 것이다. **트레이딩 자체는 영향 없다** —
equity 조회 실패 시 자동으로 페이퍼 equity로 폴백하도록 설계돼 있다
(`TRADE_MODE=paper`인 한 실주문과는 완전히 무관).

원인은 로그의 `HTTP Error 401` 뒤에 OKX가 돌려준 상세 메시지로 바로 특정된다
(`okx_auth.signed_request`가 응답 본문을 그대로 붙여서 예외를 던지도록 수정됨 —
이전 로그는 urllib의 일반 메시지만 남아 원인 불명이었다. 다음 실행부터는
`{"code":"501xx","msg":"..."}` 형태의 원문이 로그에 남는다). 코드 수정 없이도
흔한 원인은:

1. **IP 화이트리스트 (GitHub Actions에서 가장 흔함, code 50110)**: OKX API
   키에 IP 제한이 걸려 있으면, GitHub Actions 러너는 매번 다른 IP를 쓰므로
   거의 항상 거부된다. OKX API 관리 페이지에서 해당 키의 IP 화이트리스트를
   **해제**하거나 비워둔다 (Read 전용 키는 보통 무제한 허용 가능).
2. **데모/실계정 키 불일치 (code 50101 계열)**: OKX 데모 트레이딩에서 만든
   키인데 `OKX_DEMO=1`을 안 넣었거나, 반대로 실계정 키인데 `OKX_DEMO=1`을
   넣은 경우. 워크플로 env에 `OKX_DEMO`가 있는지 확인.
3. **키/시크릿/패스프레이즈 오타 또는 폐기됨 (code 50111/50113)**: GitHub
   Secrets에 붙여넣을 때 앞뒤 공백·개행이 같이 들어간 경우가 흔하다. Secrets를
   지우고 다시 입력(재입력 시 GitHub은 기존 값을 보여주지 않으니 새로 붙여넣기).
   OKX 쪽에서 키를 재발급했다면 GitHub Secrets도 같이 갱신해야 한다.
4. **타임스탬프 만료 (code 50102)**: 러너 시계 이슈 — GitHub Actions에서는
   거의 발생하지 않지만, 재시도 시에도 반복되면 의심.

---

## 보안 체크리스트

- [ ] 키는 GitHub Secrets / 로컬 env에만. **커밋·로그·스크린샷 금지.**
- [ ] OKX 키는 처음엔 **Read 전용**, 실거래 시에만 Trade + **IP 화이트리스트**.
- [ ] 실주문은 데모(`OKX_DEMO=1`)로 먼저, 그 다음 소액으로.
- [ ] 유출 의심 시 즉시 각 서비스에서 **폐기 후 재발급**.
- [ ] `journal/`에 쌓이는 로그에는 키가 들어가지 않는다(목표 비중·자본만 기록).
