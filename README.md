# jev-doom — Jev가 플레이하는 둠

VizDoom + TypeSafe Jev(System One 모델) 조합. 게임 루프는 코드가 돌리고,
Jev는 매 판정마다 **어디가 위험한지(Choice) / 지금 쏠지(Noul) / 얼마나
위험한지(Score)** 3개 질문에 답한다. 픽셀은 안 보고 게임 변수+오브젝트
정보를 텍스트 JSON으로 바꿔서 던진다.

## 실행

```sh
uv sync
# .env에 JEV_APIKEY=... (또는 TYPESAFE_API_KEY)
uv run python -m doom.play --scenario defend --episodes 2
uv run python -m doom.play --scenario simple --visible  # 화면 보기
```

## 구조

```
doom/
  doom_env.py  # VizDoom 생성 (defend/basic/simple)
  encoder.py   # 오브젝트 → 섹터 JSON (left/center/right + 근/중/원)
  policy.py    # Jev 호출 + 버튼 매핑 + 폴백 (sweep, 탄약 게이트)
  config.py    # 질문 정의, 임계값, 틱 상수
  play.py      # 에피소드 루프 + runs/*.jsonl 로그
```

## 결과 (2026-09-20, 서울에서 측정, Jev 평균 ~270ms/콜)

| 시나리오 | 결과 |
|---|---|
| defend_the_center | 매 에피소드 킬 (1,1,1,1,**4**). 26발 권총 vs 데몬이라 장기 생존은 불가 |
| simpler_basic | 2/2 승리, 무피해(hp 100). 중앙 정렬 후 1~2발로 처치 |

## 알려진 것들

- pistol은 세미오토: 발사 후 release 틱 필요 (`RELEASE_TICS`)
- 회전 속도 ~0.44°/tic 이라 길게 잡고 돌림 (`TURN_TICS=16` + sweep)
- basic 계열 몬스터는 유리몸 (1~2발 컷). 에피소드는 처치 시 종료, 처치 보상 +100
- `runs/` 로그는 pre-action 스냅샷, 최종 hp/ammo/kills는 게임 변수 기준
