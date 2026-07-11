# CRON_SETUP — cron-job.org로 정시 스캔 연결

GitHub 내장 `schedule:` cron은 부하 시 지연/누락되므로 쓰지 않는다. 대신
cron-job.org가 매 1H 마감 +3분에 `workflow_dispatch`를 POST한다.

## 1. GitHub 토큰

Fine-grained PAT 생성: 이 저장소만, 권한 **Actions: Read and write**.

## 2. cron-job.org 작업 생성

- URL: `https://api.github.com/repos/zqvo04/fabletradebot/actions/workflows/paper-trade.yml/dispatches`
- Method: `POST`
- Schedule: 매시 3분 (`3 * * * *`)
- Headers:
  - `Authorization: Bearer <PAT>`
  - `Accept: application/vnd.github+json`
  - `Content-Type: application/json`
- Body: `{"ref":"main"}`  ← 페이퍼 트랙이 도는 브랜치명으로

204 No Content가 성공이다.

## 3. 확인

저장소 Actions 탭에서 `paper-trade`가 매시 3~4분에 시작되는지 확인.
중복/누락 트리거는 무해하다 — 루프는 마감봉 데이터의 결정론적 리플레이라
같은 입력이면 같은 결과이고, 커밋 스텝은 변화 없으면 스킵한다.
