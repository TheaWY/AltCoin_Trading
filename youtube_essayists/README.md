# YouTube Essayists → Obsidian

Jenny Nicholson, Lindsay Ellis, hbomberguy 채널의 영상 목록(2026-11-06 기준)과 영상별 한 줄 설명, 그리고 transcript를 Obsidian 볼트에 넣는 도구.

| 채널 | Videos 탭 | Shorts |
|---|---|---|
| Jenny Nicholson (@JennyNicholson) | 113 | 없음 |
| Lindsay Ellis (@LindsayEllisVids) | 123 | 9 |
| hbomberguy (@hbomberguy) | 69 | 없음 |

라이브 스트림 탭은 세 채널 모두 비어 있다. 멤버십 전용, 비공개, 다른 채널(Nebula 등)에 올린 영상은 포함하지 않는다.

`data/videos.csv`가 기준 목록이다. `description_ko`는 영상 내용을 한 줄로 요약한 것이고, "(제목 기준)"이 붙은 건 내용을 확인하지 못해 제목에서 추정한 것.

## 실행 (내 컴퓨터에서)

```bash
pip install yt-dlp openpyxl
python yt_vault.py build  --vault "~/경로/내볼트"   # 노트 314개 + 채널별 MOC 생성
python yt_vault.py fetch  --vault "~/경로/내볼트"   # transcript 채우기 (수 시간 걸릴 수 있음)
python yt_vault.py verify --vault "~/경로/내볼트"   # 목록 대비 점검 → transcript_check.xlsx
```

`fetch`는 중간에 끊겨도 다시 돌리면 이미 받은 영상은 건너뛴다. 봇 확인이 뜨면 브라우저에서 내보낸 cookies.txt를 `--cookies cookies.txt`로 넘긴다.

## 점검 기준 (verify)

영상마다 다음을 보고 PASS/FAIL을 매긴다.

- 노트가 목록의 video_id와 제목으로 존재하는가
- transcript를 받을 때 yt-dlp가 돌려준 영상 id가 목록의 id와 같은가 (다른 영상 자막이 섞이지 않았는지)
- 분당 단어 수가 60–260 사이인가 (영상 길이에 비해 자막이 너무 짧거나 길면 잘못 받은 것)
- 목록에 중복이 없는가

결과는 볼트 안 `YouTube Essayists/transcript_check.xlsx`에 Videos 시트(영상별)와 Summary 시트(채널별 집계)로 저장된다.

transcript 본문은 이 저장소에 커밋하지 않는다(공개 저장소). 볼트에만 저장된다.
