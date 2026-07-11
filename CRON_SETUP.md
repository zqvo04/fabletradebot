# CRON_SETUP — 외부 스케줄러(cron-job.org)로 정확한 시각에 페이퍼 트레이딩 실행

GitHub Actions의 내장 `schedule:` cron은 러너 부하에 따라 **수 분~수십 분 지연되거나
아예 건너뛴다**. 4H 봉 마감 직후 정확히 알림을 받으려면, 외부 스케줄러가 GitHub의
`workflow_dispatch` API를 **정시에 호출**하도록 바꾸는 것이 안정적이다.
워크플로(`.github/workflows/paper-trade.yml`)는 이미 `workflow_dispatch`만으로
트리거되도록 수정돼 있다 (V5 프로파일 단독 실행).

전체 루프는 봉 마감 데이터의 **결정론적 리플레이**라, 트리거가 한 번 더 와도 결과가
같아 커밋 단계에서 no-op으로 흡수된다 — 중복 호출은 안전하다.

---

## 1. GitHub 토큰 발급 (Actions 실행 권한만)

**Fine-grained PAT (권장)** — https://github.com/settings/personal-access-tokens/new
- **Resource owner**: 본인 계정(`zqvo04`)
- **Repository access**: Only select repositories → `zqvo04/fabletradebot`
- **Permissions** → Repository permissions:
  - **Actions**: **Read and write**  ← `workflow_dispatch` 호출에 필요
  - (그 외는 전부 No access로 둬도 됨)
- **Generate token** → 값 복사 (`github_pat_...`). **이 값이 아래 `<PAT>`**.

> Classic PAT을 쓸 경우 스코프는 `repo` + `workflow`.
> ⚠️ 토큰은 커밋·이슈·스크린샷에 절대 붙여넣지 말 것. 유출 시 즉시 revoke.

## 2. cron-job.org 크론잡 생성

https://console.cron-job.org → **CREATE CRONJOB**

**Common 탭**
- **Title**: `fabletradebot v5 paper`
- **URL**:
  ```
  https://api.github.com/repos/zqvo04/fabletradebot/actions/workflows/paper-trade.yml/dispatches
  ```
- **Schedule** → **Custom** (모든 시각 UTC 기준):
  - Minutes: `7`
  - Hours: `0,4,8,12,16,20`
  - Days / Months / Weekdays: `*` (every)
  - → 4H 봉 마감(00/04/08/12/16/20 UTC) **+7분**에 실행.
    (cron-job.org의 실행 타임존을 **UTC**로 맞출 것 — 설정 하단 Timezone.)

**Advanced 탭**
- **Request method**: `POST`
- **Headers** (각 줄 Key / Value):
  | Key | Value |
  |---|---|
  | `Accept` | `application/vnd.github+json` |
  | `Authorization` | `Bearer <PAT>` |
  | `X-GitHub-Api-Version` | `2022-06-28` |
  | `Content-Type` | `application/json` |
  | `User-Agent` | `cron-job.org` |
- **Request body**: 아래를 **코드블록에서 그대로 복사**해서 붙여넣을 것
  (따옴표를 직접 타이핑하면 브라우저 자동교정이 `"`를 `“ ”`로 바꿔 400의
  가장 흔한 원인이 된다 — §3-1 참고):
  ```json
  {"ref":"main"}
  ```
  - `ref` 는 **워크플로 파일이 있고 실행할 브랜치**다. main에 이미 병합되어
    있으면 `"main"`. 아직 머지 전 브랜치에서만 테스트하려면 그 브랜치명을
    넣는다 — `workflow_dispatch`는 그 브랜치에 워크플로 파일이 존재해야 동작.
- **Save**.

## 3. 동작 확인

- **즉시 테스트**: cron-job.org 잡 상세 → **Run now** (또는 GitHub → Actions 탭 →
  paper-trade → **Run workflow**).
- 성공 시 HTTP **204 No Content** 가 돌아온다 (cron-job.org 실행 히스토리에서 확인).
  응답 본문은 비어 있는 게 정상이다.
- GitHub → **Actions** 탭에 `paper-trade` 실행이 뜨고, 몇 분 뒤 `journal/`과
  `live_data/`에 커밋이 쌓이며 Telegram/Notion 알림이 발화한다.
- **자주 나는 오류**:
  - `404` → PAT의 Actions 권한 누락, 또는 `ref` 브랜치에 워크플로 파일 없음.
  - `403` (UA 관련) → `User-Agent` 헤더 누락. GitHub API는 UA를 요구한다.
  - `422` → body의 `ref` 브랜치명이 틀림.
  - `400` → §3-1 참고.

### 3-1. `400 Bad Request` 해결 (가장 흔한 실패)

원인은 거의 항상 **Body의 JSON이 깨져 있는 것**이다 (GitHub이 돌려주는 메시지는
`"message":"Problems parsing JSON"`) — cron-job.org 잡 상세 → **History** →
실패한 실행 클릭 → **Response** 탭에서 원문 메시지를 확인할 수 있다.

1. **스마트 따옴표(가장 흔함)**: Body 입력창에 `{"ref":"main"}`을 직접 타이핑하면
   브라우저/OS 자동교정이 곧은따옴표 `"`를 굴림따옴표 `“ ”`로 바꾸는 경우가 많다.
   겉보기엔 똑같아 보이지만 JSON 파싱이 실패한다. **해결**: Body를 전부 지우고
   위 §2의 코드블록을 그대로 복사-붙여넣기 (재타이핑 금지).
2. **`Content-Type` 헤더 중복**: Headers 목록에 `Content-Type: application/json`을
   넣었는데 cron-job.org의 Body 설정 쪽에도 별도 content-type 지정이 있다면
   헤더가 두 번 전송되어 거부될 수 있다. 한쪽에서만 지정한다.
3. **Body가 비어 있거나 form 형식으로 전송됨**: `ref=main`처럼 key=value 형태로
   저장돼 있으면 JSON이 아니라 400이 난다. Body 필드가 raw JSON 모드인지 확인.
4. **직접 curl로 격리 테스트** (cron-job.org를 거치지 않고 PAT만으로 확인):
   ```bash
   curl -i -X POST \
     -H "Accept: application/vnd.github+json" \
     -H "Authorization: Bearer <PAT>" \
     -H "X-GitHub-Api-Version: 2022-06-28" \
     -H "Content-Type: application/json" \
     https://api.github.com/repos/zqvo04/fabletradebot/actions/workflows/paper-trade.yml/dispatches \
     -d '{"ref":"main"}'
   ```
   `HTTP/2 204`면 PAT·URL·워크플로 설정은 정상이고 cron-job.org 쪽 요청 포맷만
   고치면 된다. 여기서도 400이면 응답 본문의 정확한 메시지로 원인이 바로 특정된다.

## 4. (선택) GitHub 스케줄 폴백

cron-job.org가 죽어도 하루 몇 번은 자동 실행되게 하려면, 워크플로의 `on:` 블록에
아래를 되살려 넣으면 된다 (정시성은 낮지만 안전망). 결정론적 설계라 외부 트리거와
겹쳐도 무해하다:

```yaml
on:
  workflow_dispatch: {}
  schedule:
    - cron: "7 0,4,8,12,16,20 * * *"   # 부정확한 폴백 (지연/누락 가능)
```

---

관련: 시크릿(Notion/Telegram/OKX) 설정은 [SECRETS.md](SECRETS.md),
V5 시스템 설계는 [REDESIGN_V5.md](REDESIGN_V5.md).
