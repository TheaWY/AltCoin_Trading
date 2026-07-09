# 설치 — 커서 없이, 맥미니에서 바로 (5분)

레포 루트에서:

```bash
unzip -o ~/Downloads/research_stack_v2.zip     # 새 파일만 추가됨
python3 ops/apply_patches.py                    # 네 기존 파일 4개에 소규모 패치 (멱등, 재실행 안전)
pip3 install pyyaml                             # 유일한 신규 의존성
python3 scripts/test_promotion.py               # All 8 checks passed
python3 scripts/test_research_stack.py          # All 18 checks passed
python3 scripts/test_robust_layer.py            # All 18 checks passed
bash ops/install.sh                             # pmset + launchd (워커 상시, 리서치 01:00, 워치독 60초)
```

끝. 대시보드 하단 탭에 "실험"이 생기고 /experiments 페이지가 15초마다 갱신되며
시스템이 방금 무엇을 왜 결정했는지(실험 시작/완료, 게이트 차단 사유, DSR 수치,
승격/롤백, 감쇠 경고, 시행예산 거부) 실시간으로 보여준다.

## 무엇이 자기교정을 하나

밤마다: generator가 가설 큐 생성 → runner가 MinBTL 시행예산 안에서 워크포워드
실행 → fresh eval(실험 생성 이후 데이터로 재검증) → 승격 게이트 3중
(워크포워드 일관성 → Deflated Sharpe(시행횟수 N 보정) → 신선 데이터) →
통과 시 자동 승격+워커 재시작. 매분: 워치독이 health 체크 — 기대값 음수/낙폭
초과 시 자동 롤백, 감쇠(live가 백테스트의 50% 미만) 감지 시 경고.

## 수동 커맨드

```bash
python3 -m src.research.event_study --symbols BTC/USDT,ETH/USDT --days 365
python3 -m src.research.generator --dry-run
python3 -m src.research.runner --max-runs 20
python3 -m src.research.promotion status|promote-if-ready|health|rollback
python3 -m src.research.paper_analysis
python3 -m src.research.data_quality --scan
bash ops/nightly_research.sh                    # 야간 배치 수동 실행
```

## 알아둘 것 두 가지

1. **시행예산이 지금은 매우 작다.** 현재 워크포워드 히스토리(6윈도우×60일≈1년)로는
   MinBTL상 config 3~4개만 통계적으로 허용된다(논문 앵커: 5년↔45개). 러너가 이걸
   강제하고 초과 시 거부를 결정 피드에 기록한다. data.binance.vision에서 2020년
   부터 히스토리를 적재해 RESEARCH_WINDOW_COUNT를 늘리는 게 예산을 키우는 길이다.
   급하면 RESEARCH_TRIAL_BUDGET=45 처럼 수동 오버라이드 가능(과적합 리스크 본인 부담).
2. **DSR 게이트는 시행이 쌓일수록 엄격해진다.** N이 커지면 운으로 넘어야 할
   허들(SR0)이 올라간다 — 실험을 많이 돌릴수록 승격이 어려워지는 게 정확히
   의도된 동작이다.
