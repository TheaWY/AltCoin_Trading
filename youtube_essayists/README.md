# YouTube Essayists → Obsidian

Video lists for Jenny Nicholson, Lindsay Ellis and hbomberguy (as of 2026-11-06), a one-line summary of each video, and a tool that puts the notes and transcripts into an Obsidian vault.

| Channel | Videos tab | Shorts |
|---|---|---|
| Jenny Nicholson (@JennyNicholson) | 113 | none |
| Lindsay Ellis (@LindsayEllisVids) | 123 | 9 |
| hbomberguy (@hbomberguy) | 69 | none |

All three Live tabs are empty. Members-only, private, and off-YouTube (e.g. Nebula) videos are not included.

`data/videos.csv` is the reference list. `description` is a one-line summary; entries marked "(guessed from title)" are inferred from the title because the content couldn't be confirmed.

## Running it (on your own computer)

```bash
pip install yt-dlp openpyxl
python yt_vault.py build  --vault "/path/to/vault"   # 314 notes + one index note per channel
python yt_vault.py fetch  --vault "/path/to/vault"   # fill in transcripts (can take hours)
python yt_vault.py verify --vault "/path/to/vault"   # check against the list -> transcript_check.xlsx
```

`fetch` is resumable: rerun it and it skips videos that already have a transcript. If YouTube shows a bot check, export cookies.txt from your browser and pass `--cookies cookies.txt`.

## What verify checks

Each video gets PASS or FAIL based on:

- a note exists with the list's video_id and title
- the video id yt-dlp reported when downloading the transcript matches the list (no captions from the wrong video)
- words per minute is between 60 and 260 (far outside that means the wrong or a broken transcript)
- no duplicates in the list

Results go to `YouTube Essayists/transcript_check.xlsx` inside the vault: a Videos sheet (one row per video) and a Summary sheet (counts per channel).

Transcript text is never committed to this repo (it's public). It only goes into your vault.
