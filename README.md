# jev-doom — Jev가 플레이하는 둠

VizDoom + TypeSafe Jev(System One 모델) 조합. 게임 루프는 코드가 돌리고,
Jev는 매 판정마다 **어디가 위험한지(Choice) / 지금 쏠지(Noul) / 얼마나
위험한지(Score)** 3개 질문에 답한다. 픽셀은 안 보고 게임 변수+오브젝트
정보를 텍스트 JSON으로 바꿔서 던진다.

## 플레이 영상 (defend_the_center, 3킬)

![Jev 플레이](assets/defend_ep.gif)

풀 화질: [assets/defend_ep.mp4](assets/defend_ep.mp4) (판정 1회당 1프레임, 8fps 타임랩스)

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

## 비용 (실측, 53판정 defend 에피소드 기준)

판정 1회 평균 input 633토큰 / output 68토큰, 평균 280ms.

| 모델 | input $/MTok | output $/MTok | 에피소드당 | Jev 대비 |
|---|---|---|---|---|
| **Jev (실측)** | 0.042 | **무료** | **$0.0014** | 1x |
| GPT-5.6 Luna | 0.20 | 1.20 | $0.0110 | 8x |
| GPT-5.4 Nano | 0.20 | 1.25 | $0.0112 | 8x |
| Claude Haiku 4.5 | 1.00 | 5.00 | $0.0516 | 37x |
| GPT-5.6 Terra (동급 지능) | 2.00 | 12.00 | $0.1104 | 78x |

같은 속도로 1시간 돌리면(초당 ~3.5판정): Jev **$0.34** vs Terra $26.2.
주의: LLM 쪽은 구조화 JSON만 내보낸다는 하한 가정. 실제로는 추론 토큰이
붙어서 더 비싸지고, 응답도 초 단위로 느려진다 (Jev 실측 280ms).

가격 출처: TypeSafe 블로그(Jev), OpenAI/Anthropic 공개 요금 (2026-09 기준).

## 알려진 것들

- pistol은 세미오토: 발사 후 release 틱 필요 (`RELEASE_TICS`)
- 회전 속도 ~0.44°/tic 이라 길게 잡고 돌림 (`TURN_TICS=16` + sweep)
- basic 계열 몬스터는 유리몸 (1~2발 컷). 에피소드는 처치 시 종료, 처치 보상 +100
- `runs/` 로그는 pre-action 스냅샷, 최종 hp/ammo/kills는 게임 변수 기준
