# Week 06 REPORT: 하네스를 A2A 뒤에 seller와 buyer로 세우기

학번 26510127 · Chaewon Lee (@chaewoonee)

> 초안입니다. (3)~(5)의 해석은 제 말로 다듬어 제출합니다.

## (1) 하네스, 모델, 명령

| 항목 | 값 |
|---|---|
| 하네스 | Claude Code CLI, 비대화형 |
| 모델 | haiku (`--model haiku`) |
| 정확한 명령 | `claude -p --model haiku --tools "" --setting-sources "" --no-session-persistence` (프롬프트는 stdin) |
| 실행 위치 | 호출마다 새 빈 임시 디렉터리(`harness.py`). 프로젝트의 CLAUDE.md·AGENTS.md를 읽지 않고, seller와 buyer가 세션을 공유하지 않는다 |
| 바꾸는 법 | 환경변수 `HARNESS_CMD` |
| 실행 환경 | Linux, Python 3.11, `a2a-sdk[http-server]`, uvicorn, httpx |

```bash
python scripts/trade_test_week06.py submissions/26510127/week-06     # 21/21
python submissions/26510127/week-06/challenge1.py                     # 8/8
python submissions/26510127/week-06/challenge2.py                     # 5/5 bought, 615 / 650
```

## (2) 각 에이전트가 결정하는 방식

두 에이전트 모두 하네스에게 **JSON 한 개**만 답하게 했다. 4주차에서 같은 계열 모델로 free·tagged·structured를 비교했을 때 structured(JSON)만 형식 오류가 0이었기 때문이다. 답은 `harness.ask_json`이 첫 JSON 객체를 꺼내 읽는다.

| | seller (`seller.py`) | buyer (`buyer.py`) |
|---|---|---|
| 프롬프트에 들어가는 것 | 품목, reserve, 지금이 buyer의 몇 번째 메시지인지, 대화 전문 | 품목, budget, 가게 카드 설명, 지금까지 낸 제안 수, 대화 전문 |
| 답 형식 | `{"action":"counter","price":N}` / `{"action":"accept"}` / `{"action":"reject"}` | `{"action":"offer","price":N}` / `{"action":"walk"}` |
| 하네스가 정하는 것 | 첫 호가, 매 역제안, 수락·거절 시점 | 매 제안 금액, 떠날 시점. 가게 호가를 받아들이려면 그 금액을 그대로 제안 |
| 코드가 지키는 것 | reserve 아래 호가 → reserve로 올림, reserve 아래 수락 → 직전 호가로 역제안 (`[guard]`) | budget 위 제안 → budget으로 내림 (`[guard]`), 제안 8번 뒤 CancelTask |
| 코드 규칙 | buyer 제안이 내 현재 호가 이상이면 수락 (`[rule]`) | 없음 |

한도는 **프롬프트와 코드 양쪽**에 두었다. 결과적으로 최종 trade test와 챌린지 실행 전체에서 `[guard]`는 한 번도 발동하지 않았다. haiku가 프롬프트의 reserve를 스스로 지켰고(`seller_holds_reserve`의 호가가 `[380, 340, 300, 280, 265, 262, 260, 260, 260]`처럼 260에서 멈춤), budget을 넘는 제안도 하지 않았다. 코드 가드는 "지켜진 것을 확인하는 장치"로만 남았다. 코드 규칙 `[rule]`은 seller_deal 3번에서 발동했다(`[rule] offer 115 meets our ask 115 -> accept`). 테스트 구매자는 호가를 수락할 때 그 숫자를 보내는데, 이것을 하네스에게 다시 묻지 않고 거래로 닫는다.

## (3) 통과하기까지 실패한 것과 바꾼 것

trade test는 첫 실행에서 21/21이었다. 실패는 챌린지에서 나왔고, 모든 시도를 지우지 않고 `logs/challenge-*/` 아래에 남겼다.

1. **중첩 JSON을 못 읽음** (`logs/challenge-2/attempt-1-nested-json/`). 하네스는 계획을 제대로 냈다.
   `[plan] [harness 3575 chars] {"plan": {"0": [6, 15, 5], "1": [2, 0, 1], ...}}`
   그런데 `ask_json`의 정규식 `\{[^{}]*\}`가 안쪽 `{"0": ...}`만 잡아 `plan` 키를 찾지 못했고, 모든 품목의 가게 목록이 비어 `RESULT {"bought": {}, ...}`로 끝났다. `json.JSONDecoder.raw_decode`로 각 `{`에서 끝까지 해석하도록 바꿨다.
2. **검색이 엉뚱한 범주를 가져옴 + 고르기 프롬프트가 너무 엄격함** (`attempt-2-search-and-strict-pick/`). 5개 중 3개만 샀다.
   - "a reading lamp for my desk"는 `desk`가 범주 이름이라 후보 8개가 전부 책상이었고, 하네스는 맞게 `{"action": "none"}`을 답했다.
   - "a comfortable office chair"는 후보가 모두 의자였는데도 `none`이었다. 설명에 "comfortable", "office"가 없어서다.
   - 검색 결과에서 한 범주가 최대 4개만 차지하게 하고, 고르기 프롬프트에 "soft words는 soft하게, 종류나 hard constraint가 안 맞을 때만 none"을 적었다.
3. **숫자가 엉뚱한 속성에 붙음** (`logs/challenge-1/run-2/`). 2번 수정 뒤 챌린지 1이 7/8로 떨어졌다.
   `item: boo-00075 Urban Compact Birch Bookshelf in Honey | reserve 115 | fits request: False`
   요청 "about 40 books, not taller than my desk (75 cm)"의 75가 "holds about 75 books"에 가산점을 줬고, 범주 상한 때문에 남은 책장 후보 4개가 모두 75권짜리였다. 숫자는 바로 뒤의 단어(`40 books`, `75 cm`)에만 붙이고, `not taller/under/at least`와 cm를 읽어 높이 조건에 점수를 주도록 고쳤다. 이후 8/8.

## (4) A2A가 정해 준 것, 밖에서 따로 맞춰야 했던 것

A2A가 정해 준 것은 대화의 뼈대다. 협상 하나는 task 하나이고, 상태가 `INPUT_REQUIRED`(값이 열려 있음) → `COMPLETED`(영수증 artifact) / `REJECTED`(결렬)로 바뀌고, 이어지는 메시지는 `taskId`로 같은 협상에 붙고, 끝난 task에 메시지를 보내면 오류가 오고, 상대가 무엇을 하는지는 Agent Card로 미리 안다. 그래서 buyer는 상대가 어떤 하네스인지 몰라도 같은 코드로 테스트 가게, 내 가게, 20개 가게와 거래했다. A2A 밖에서 맞춰야 했던 것은 **값의 의미**다. 가격은 메시지 텍스트의 "마지막 숫자"라는 약속(그래서 카탈로그 상품 이름에 숫자를 넣지 않았다), "수락"은 상대 호가를 그대로 다시 보내는 것이라는 약속, 영수증 data Part의 필드 이름(`price`, `item`)과 숫자 형식(보낸 115가 `115.0`으로 돌아온다), 처음 메시지에서 품목을 어떻게 말하는지(정확한 이름인지 설명인지), 떠날 때 CancelTask를 보낼지 그냥 멈출지. 4주차의 performative가 A2A에서는 task 상태로 옮겨 갔지만, content(가격) 쪽은 여전히 양쪽이 미리 합의한 관례에 기대고 있다.

## (5) 챌린지 1, 2

### 챌린지 1: 3120개 카탈로그

- `make_catalog.py`(seed 고정)가 12개 범주 × 재질·색·스타일·크기 조합으로 3120개를 만든다. 각 항목에는 높이·폭·수용량(책 권수, 좌석, 인원), 설명, reserve, 정가가 있다(`catalog.json`).
- 하네스는 카탈로그 전체를 보지 않는다. `catalog.py`의 검색이 동의어(wooden→oak/pine/…, couch→sofa)와 "숫자+속성" 매칭으로 후보를 8개로 줄이고, 하네스는 `{"action":"pick","id":...}` 또는 `{"action":"none"}`만 답한다. 고른 항목의 reserve로 이후 협상을 하고, reserve 가드는 그대로다.
- 결과(`logs/challenge-1/run.log`): 8/8. 7개 요청은 조건을 모두 만족하는 항목을 골랐고, 카탈로그에 없는 "marble bar stool"은 `none`으로 거절했다. 호가가 reserve 아래로 내려간 경우는 0이다. 요청 하나에 하네스 호출은 고르기 1번 + 협상 3~4번이다.

### 챌린지 2: 가게 20곳, 장보기 목록 5개, 총예산 650

- `challenge2.py`가 `seller.py`를 20개 띄운다. 가게마다 2~4개 범주를 팔고, reserve 배율이 0.9~1.35로 다르다.
- `shopper.py`의 흐름:
  1. 카드 20장을 읽고 하네스 1번으로 품목별 후보 가게 순위를 받는다(DF).
  2. 품목마다 상위 2개 가게에 동시에 문의해 첫 호가를 받는다.
  3. 하네스 1번으로 품목별 가게와 상한(cap)을 정한다. 상한 합계가 예산을 넘으면 코드가 비율로 줄인다.
  4. 5개 협상을 동시에 진행한다.
- **상태 전달**:
  - 하네스는 대화 전문을 보지 않는다. 협상마다 `{shop, item, asks, offers, want}` 요약과, 5개 품목의 한 줄짜리 장부(산 것·진행 중·대기), 남은 예산만 받는다.
  - 대화 전문은 로그에만 남는다.
  - 그래서 수를 둘 때마다 보내는 프롬프트는 협상이 길어져도 901~1171자로 거의 일정했다. 카드 20장을 한 번에 보는 계획 프롬프트만 3575자다.
- **코드가 지키는 것**: 품목별 cap 이하 제안, 남은 예산 이하 제안, 품목당 한 번만 구매(lock).
- **결과**(`logs/challenge-2/shopper.log`의 마지막 줄 `RESULT`):

| 품목 | 가게 | 상품 | 가격 |
|---|---|---|---|
| a reading lamp for my desk | North Studio | Minimal Low Glass Lamp in Honey | 33 |
| a comfortable office chair | Oak & Ash | Japandi Wide Velvet Chair in Cream | 161 |
| a bookshelf for about 60 books | The Reading Room | Coastal Wide Oak Bookshelf in Navy | 232 |
| a small wool rug for the bedroom | Maple Street | Loft Standard Wool Rug in Olive | 81 |
| a mirror for the hallway | Hallway Goods | Classic Grand Oak Mirror in Terracotta | 108 |

- 5개를 모두 샀고, 합계 615로 예산 650 이내이며, 중복 구매는 없다. 하네스 호출 23번, 104초가 걸렸다.
- 배정 단계의 하네스 출력: `{"choice": {"0": {"shop": "North Studio", "cap": 34}, "1": {"shop": "Oak & Ash", "cap": 165}, ...}}`
