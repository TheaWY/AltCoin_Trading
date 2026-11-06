#!/usr/bin/env python3
"""Build Obsidian notes for a list of YouTube videos, fill in transcripts, and
verify the result in an Excel workbook.

    python yt_vault.py build  --vault ~/Obsidian/MyVault
    python yt_vault.py fetch  --vault ~/Obsidian/MyVault [--cookies cookies.txt] [--limit N]
    python yt_vault.py verify --vault ~/Obsidian/MyVault [--out check.xlsx]

Needs: pip install yt-dlp openpyxl
Run `fetch` on your own computer. YouTube refuses cloud/datacenter IPs with a
bot check, and residential connections normally work without cookies.
"""
import argparse
import csv
import json
import re
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
LIST_CSV = HERE / "data" / "videos.csv"
ROOT = "YouTube Essayists"
MARK = "<!-- transcript -->"


# ---------- helpers ----------
def load_list():
    with open(LIST_CSV, encoding="utf-8-sig") as f:
        return list(csv.DictReader(f))


def safe(name, n=90):
    name = re.sub(r'[\\/:*?"<>|#^\[\]]', "", name).strip().rstrip(".")
    return name[:n].strip()


def note_path(vault, r):
    return Path(vault) / ROOT / r["channel"] / f"{safe(r['title'])} [{r['video_id']}].md"


def hms(sec):
    try:
        s = int(float(sec))
    except (TypeError, ValueError):
        return ""
    return f"{s // 3600:02d}:{s % 3600 // 60:02d}:{s % 60:02d}"


def yq(s):
    return json.dumps(str(s), ensure_ascii=False)


def read_front(text):
    m = re.match(r"---\n(.*?)\n---\n", text, re.S)
    out = {}
    if m:
        for line in m.group(1).splitlines():
            if ":" in line:
                k, v = line.split(":", 1)
                v = v.strip()
                try:
                    v = json.loads(v)
                except ValueError:
                    pass
                out[k.strip()] = v
    return out


def write_note(path, r, status="pending", meta=None, body=None):
    meta = meta or {}
    fm = {
        "title": r["title"],
        "channel": r["channel"],
        "type": r["type"],
        "video_id": r["video_id"],
        "url": r["url"],
        "duration": hms(r["duration_sec"]),
        "upload_order": int(r["order"]),
        "about": r["description"],
        "transcript_status": status,
        "transcript_video_id": meta.get("transcript_video_id", ""),
        "transcript_lang": meta.get("transcript_lang", ""),
        "transcript_kind": meta.get("transcript_kind", ""),
        "transcript_words": meta.get("transcript_words", 0),
        "upload_date": meta.get("upload_date", ""),
        "tags": ["youtube", "video-essay", safe(r["channel"]).replace(" ", "-").lower()],
    }
    lines = ["---"] + [f"{k}: {json.dumps(v, ensure_ascii=False)}" for k, v in fm.items()] + ["---", ""]
    lines += [f"# {r['title']}", "", f"Channel: [[{r['channel']}]] · Length {fm['duration']} · [YouTube]({r['url']})", ""]
    lines += ["## What it is", "", r["description"], "", "## Transcript", "", MARK, ""]
    lines.append(body if body else "_Not fetched yet. Run `python yt_vault.py fetch`._")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


# ---------- build ----------
def cmd_build(a):
    rows = load_list()
    made = kept = 0
    for r in rows:
        p = note_path(a.vault, r)
        if p.exists() and read_front(p.read_text(encoding="utf-8")).get("transcript_status") == "ok":
            kept += 1
            continue
        write_note(p, r)
        made += 1
    by = {}
    for r in rows:
        by.setdefault(r["channel"], []).append(r)
    for ch, rs in by.items():
        rs.sort(key=lambda r: (r["type"] != "video", -int(r["order"])))
        out = [f"# {ch}", "", f"{sum(r['type']=='video' for r in rs)} videos" +
               (f" + {sum(r['type']=='short' for r in rs)} Shorts" if any(r['type'] == 'short' for r in rs) else ""), "",
               "| # | Video | Length | What it is |", "|---|---|---|---|"]
        for r in rs:
            link = f"[[{note_path(a.vault, r).stem}\\|{r['title'].replace('|', '/')}]]"
            out.append(f"| {r['order']} | {link} | {hms(r['duration_sec'])} | {r['description'].replace('|', '/')} |")
        (Path(a.vault) / ROOT / ch / f"{ch}.md").write_text("\n".join(out) + "\n", encoding="utf-8")
    idx = [f"# {ROOT}", ""] + [f"- [[{ch}]] ({len(rs)})" for ch, rs in by.items()]
    (Path(a.vault) / ROOT / f"{ROOT}.md").write_text("\n".join(idx) + "\n", encoding="utf-8")
    print(f"notes written: {made}, kept (already had transcript): {kept}")


# ---------- fetch ----------
def parse_json3(path):
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    segs = []
    for ev in data.get("events", []):
        txt = "".join(s.get("utf8", "") for s in ev.get("segs", []) or []).replace("\n", " ").strip()
        if txt:
            segs.append((ev.get("tStartMs", 0) / 1000, txt))
    # merge into ~30s paragraphs, drop exact repeats from rolling auto captions
    paras, cur, start, last = [], [], None, None
    for t, txt in segs:
        if txt == last:
            continue
        last = txt
        if start is None:
            start = t
        cur.append(txt)
        if t - start >= 30:
            paras.append((start, " ".join(cur)))
            cur, start = [], None
    if cur:
        paras.append((start, " ".join(cur)))
    return paras


def fetch_one(r, cookies, tmp):
    cmd = ["yt-dlp", "--skip-download", "--write-subs", "--write-auto-subs",
           "--sub-langs", "en,en-US,en-GB,en-orig", "--sub-format", "json3",
           "--write-info-json", "-o", str(Path(tmp) / "%(id)s.%(ext)s"), r["url"]]
    if cookies:
        cmd[1:1] = ["--cookies", cookies]
    p = subprocess.run(cmd, capture_output=True, text=True)
    info_f = Path(tmp) / f"{r['video_id']}.info.json"
    if not info_f.exists():
        return None, (p.stderr.strip().splitlines() or ["unknown error"])[-1]
    info = json.loads(info_f.read_text(encoding="utf-8"))
    manual = set((info.get("subtitles") or {}).keys())
    subs = sorted(Path(tmp).glob(f"{r['video_id']}.*.json3"))
    if not subs:
        return {"upload_date": info.get("upload_date", "")}, "no English subtitles"
    pick = next((s for s in subs if s.suffixes[-2][1:] in manual), subs[0])
    lang = pick.suffixes[-2][1:]
    paras = parse_json3(pick)
    body = "\n\n".join(f"**{hms(t)}** {txt}" for t, txt in paras)
    d = info.get("upload_date", "")
    return {
        "transcript_video_id": info.get("id", ""),
        "transcript_lang": lang,
        "transcript_kind": "manual" if lang in manual else "auto",
        "transcript_words": len(body.split()),
        "upload_date": f"{d[:4]}-{d[4:6]}-{d[6:]}" if len(d) == 8 else d,
        "body": body,
    }, None


def cmd_fetch(a):
    rows = load_list()
    done = fail = 0
    for r in rows:
        p = note_path(a.vault, r)
        if not p.exists():
            write_note(p, r)
        if read_front(p.read_text(encoding="utf-8")).get("transcript_status") == "ok" and not a.force:
            continue
        if a.limit and done + fail >= a.limit:
            break
        with tempfile.TemporaryDirectory() as tmp:
            meta, err = fetch_one(r, a.cookies, tmp)
        if err:
            fail += 1
            write_note(p, r, status="failed", meta=meta or {}, body=f"_Fetch failed: {err}_")
            print(f"FAIL {r['video_id']} {r['title'][:50]} :: {err}")
        else:
            done += 1
            write_note(p, r, status="ok", meta=meta, body=meta.pop("body"))
            print(f"ok   {r['video_id']} {r['title'][:50]} ({meta['transcript_words']} words, {meta['transcript_kind']})")
    print(f"fetched {done}, failed {fail}")


# ---------- verify ----------
def cmd_verify(a):
    from openpyxl import Workbook
    from openpyxl.styles import Font, PatternFill
    from openpyxl.utils import get_column_letter

    rows = load_list()
    seen_ids = {}
    out = []
    for r in rows:
        p = note_path(a.vault, r)
        rec = dict(r)
        rec["note_file"] = str(p.relative_to(Path(a.vault)))
        issues = []
        if not p.exists():
            rec.update(note_exists="N", transcript_status="missing", words=0, wpm="")
            issues.append("note missing")
        else:
            fm = read_front(p.read_text(encoding="utf-8"))
            rec["note_exists"] = "Y"
            rec["transcript_status"] = fm.get("transcript_status", "")
            rec["transcript_video_id"] = fm.get("transcript_video_id", "")
            rec["transcript_kind"] = fm.get("transcript_kind", "")
            rec["upload_date"] = fm.get("upload_date", "")
            words = int(fm.get("transcript_words") or 0)
            rec["words"] = words
            if fm.get("video_id") != r["video_id"]:
                issues.append("note video_id mismatch")
            if fm.get("title") != r["title"]:
                issues.append("note title mismatch")
            st = rec["transcript_status"]
            if st == "ok":
                if rec["transcript_video_id"] != r["video_id"]:
                    issues.append("transcript belongs to a different video")
                mins = (float(r["duration_sec"]) / 60) if r["duration_sec"] else 0
                wpm = round(words / mins) if mins else ""
                rec["wpm"] = wpm
                if wpm != "" and not (60 <= wpm <= 260):
                    issues.append(f"{wpm} words/min is out of range (too short or long for the video length)")
                if words < 30:
                    issues.append("transcript nearly empty")
            else:
                rec["wpm"] = ""
                issues.append("transcript not fetched" if st == "pending" else "transcript fetch failed")
        if r["video_id"] in seen_ids:
            issues.append("duplicate in list")
        seen_ids[r["video_id"]] = 1
        rec["check"] = "PASS" if not issues else "FAIL"
        rec["issues"] = "; ".join(issues)
        out.append(rec)

    wb = Workbook()
    ws = wb.active
    ws.title = "Videos"
    cols = ["channel", "type", "order", "video_id", "title", "url", "duration_sec", "description",
            "note_exists", "transcript_status", "transcript_video_id", "transcript_kind", "upload_date",
            "words", "wpm", "check", "issues", "note_file"]
    ws.append(cols)
    for c in ws[1]:
        c.font = Font(bold=True)
    red, green = PatternFill("solid", fgColor="F8D7DA"), PatternFill("solid", fgColor="D4EDDA")
    for rec in out:
        ws.append([rec.get(c, "") for c in cols])
        ws.cell(ws.max_row, cols.index("check") + 1).fill = green if rec["check"] == "PASS" else red
    for i, w in enumerate([16, 7, 6, 14, 50, 44, 10, 50, 8, 12, 14, 8, 11, 8, 6, 7, 40, 60], 1):
        ws.column_dimensions[get_column_letter(i)].width = w
    ws.freeze_panes = "A2"
    ws.auto_filter.ref = ws.dimensions

    s = wb.create_sheet("Summary")
    s.append(["channel", "type", "listed", "notes", "transcript ok", "id match", "PASS", "FAIL"])
    for c in s[1]:
        c.font = Font(bold=True)
    keys = sorted({(r["channel"], r["type"]) for r in out}, key=lambda k: (k[0], k[1] != "video"))
    for ch, t in keys + [("TOTAL", "")]:
        g = [r for r in out if ch == "TOTAL" or (r["channel"] == ch and r["type"] == t)]
        s.append([ch, t, len(g), sum(r["note_exists"] == "Y" for r in g),
                  sum(r["transcript_status"] == "ok" for r in g),
                  sum(r.get("transcript_video_id") == r["video_id"] for r in g),
                  sum(r["check"] == "PASS" for r in g), sum(r["check"] == "FAIL" for r in g)])
    for i in range(1, 9):
        s.column_dimensions[get_column_letter(i)].width = 16
    dest = Path(a.out or (Path(a.vault) / ROOT / "transcript_check.xlsx"))
    wb.save(dest)
    tot = s.max_row
    print("saved", dest)
    for row in s.iter_rows(values_only=True):
        print("\t".join(str(v) for v in row))


def main():
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name in ("build", "fetch", "verify"):
        sp = sub.add_parser(name)
        sp.add_argument("--vault", required=True, help="Obsidian vault folder")
        if name == "fetch":
            sp.add_argument("--cookies", help="Netscape cookies.txt exported from your browser (optional)")
            sp.add_argument("--limit", type=int, default=0)
            sp.add_argument("--force", action="store_true", help="re-fetch notes that already have transcripts")
        if name == "verify":
            sp.add_argument("--out")
    a = ap.parse_args()
    a.vault = str(Path(a.vault).expanduser())
    {"build": cmd_build, "fetch": cmd_fetch, "verify": cmd_verify}[a.cmd](a)


if __name__ == "__main__":
    sys.exit(main())
