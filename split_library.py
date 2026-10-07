#!/usr/bin/env python3
"""
split_library.py - split a flat directory of PDFs into one PDF per chapter,
using each file's embedded outline (bookmarks). Files without a usable outline
are not split; they are logged with a reason instead.

    python split_library.py SRC [DST] [--dry-run] [--limit N] [--workers N] [--retry]

SRC   flat directory of PDFs (never modified)
DST   output directory, default: a sibling of SRC named "<SRC>-split"

Output layout:

    DST/
      _log/
        results.jsonl      one line per source file examined (see README)
        summary.txt        counts per outcome and reason, rewritten each run
      example/
        example - [frontmatter].pdf     pages before the first chapter, if any
        example - Introduction.pdf
        example - Chapter 1.pdf

Requires Python 3.9+ and PyMuPDF (pip install pymupdf). No OCR, no printed-TOC
parsing: a file is split only if it carries bookmarks that look like chapters.
"""

from __future__ import annotations

import argparse
import json
import multiprocessing as mp
import os
import random
import re
import shutil
import sys
import time
import unicodedata
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

import pymupdf

SCRIPT_VERSION = "1.0"

# ---- sanity thresholds for "does this outline look like chapters?" ----------
MIN_CHAPTERS = 2          # fewer than this: suspect (single_chapter)
MAX_CHAPTER_RATIO = 0.50  # chapters > this fraction of pages: suspect (too_many_chapters)
MAX_DOMINANT_RATIO = 0.95 # one chapter > this fraction of pages: suspect (one_chapter_dominates)

MAX_FILENAME_BYTES = 255
FRONTMATTER_TITLE = "[frontmatter]"
_BAD_CHARS = re.compile(r'[\\/:*?"<>|\x00-\x1f\x7f]')


# ---------------------------------------------------------------------------
# Naming
# ---------------------------------------------------------------------------

def clean_title(title: str) -> str:
    t = unicodedata.normalize("NFC", title or "")
    t = _BAD_CHARS.sub("-", t)
    t = re.sub(r"\s+", " ", t).strip().rstrip(".").strip()
    return t or "untitled"


def chapter_filename(book: str, title: str) -> str:
    """'<book> - <title>.pdf', truncating the title so the whole name fits in 255 bytes."""
    prefix = f"{book} - "
    suffix = ".pdf"
    budget = MAX_FILENAME_BYTES - len(prefix.encode()) - len(suffix.encode())
    t = title
    while len(t.encode()) > budget and t:
        t = t[:-1]
    return f"{prefix}{t.rstrip()}{suffix}"


def dedupe_names(names: list[str]) -> list[str]:
    seen: Counter = Counter()
    out = []
    for n in names:
        key = n.casefold()
        seen[key] += 1
        if seen[key] == 1:
            out.append(n)
        else:
            stem, ext = os.path.splitext(n)
            out.append(f"{stem} ({seen[key]}){ext}")
    return out


# ---------------------------------------------------------------------------
# Outline analysis
# ---------------------------------------------------------------------------

def choose_split_level(toc: list[list], page_count: int):
    """Return (level, chapters) where chapters = [(title, start_page), ...] 1-based,
    one per distinct start page, sorted. Level is the shallowest outline level that
    has at least 2 distinct start pages; if none has, the shallowest level with any
    valid entry (which will then fail the single_chapter check)."""
    valid = [(lvl, title, page) for lvl, title, page in toc if 1 <= page <= page_count]
    if not valid:
        return None, []
    levels = sorted({lvl for lvl, _, _ in valid})
    chosen = None
    for lvl in levels:
        pages = {p for l, _, p in valid if l == lvl}
        if len(pages) >= 2:
            chosen = lvl
            break
    if chosen is None:
        chosen = levels[0]
    first_title_by_page: dict[int, str] = {}
    for l, title, p in valid:
        if l == chosen and p not in first_title_by_page:
            first_title_by_page[p] = title
    chapters = sorted(first_title_by_page.items(), key=lambda kv: kv[0])
    return chosen, [(title, page) for page, title in chapters]


def sanity_check(chapters: list[tuple[str, int]], page_count: int) -> str | None:
    n = len(chapters)
    if n < MIN_CHAPTERS:
        return "single_chapter"
    if n > page_count * MAX_CHAPTER_RATIO:
        return "too_many_chapters"
    starts = [p for _, p in chapters] + [page_count + 1]
    longest = max(starts[i + 1] - starts[i] for i in range(n))
    if longest > page_count * MAX_DOMINANT_RATIO:
        return "one_chapter_dominates"
    return None


def build_sections(chapters: list[tuple[str, int]], page_count: int):
    """[(title, start, end)] 1-based inclusive, with a [frontmatter] section if needed."""
    sections = []
    if chapters[0][1] > 1:
        sections.append((FRONTMATTER_TITLE, 1, chapters[0][1] - 1))
    for i, (title, start) in enumerate(chapters):
        end = chapters[i + 1][1] - 1 if i + 1 < len(chapters) else page_count
        sections.append((title, start, end))
    return sections


def nested_bookmarks(toc: list[list], split_level: int, start: int, end: int):
    """Bookmarks below the split level inside [start, end], re-based for the chapter file.
    Returns a list suitable for Document.set_toc(), or [] if the hierarchy is irregular."""
    out = []
    for lvl, title, page in toc:
        if lvl > split_level and start <= page <= end:
            out.append([lvl - split_level, re.sub(r"\s+", " ", title or "").strip(), page - start + 1])
    # set_toc requires the first entry at level 1 and no level jumps > 1
    prev = 0
    for entry in out:
        if entry[0] > prev + 1:
            return []
        prev = entry[0]
    return out


# ---------------------------------------------------------------------------
# Per-file processing (runs in a worker process)
# ---------------------------------------------------------------------------

def process_file(args: tuple) -> dict:
    src, dst_root, dry_run, skip_repaired = args
    src = Path(src)
    dst_root = Path(dst_root)
    book = src.stem
    t0 = time.monotonic()
    rec = {
        "book": book,
        "source": str(src),
        "outcome": None,
        "reason": None,
        "pages": None,
        "outline_entries": None,
        "outline_depth": None,
        "split_level": None,
        "chapters": None,
        "repaired": None,
        "producer": None,
        "creator": None,
        "size_bytes": None,
        "seconds": None,
        "script_version": SCRIPT_VERSION,
        "timestamp": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }

    def done(outcome, reason=None):
        rec["outcome"] = outcome
        rec["reason"] = reason
        rec["seconds"] = round(time.monotonic() - t0, 3)
        return rec

    try:
        rec["size_bytes"] = src.stat().st_size
    except OSError as e:
        return done("error", f"stat: {e}")

    try:
        doc = pymupdf.open(src)
    except Exception as e:  # noqa: BLE001
        return done("skipped", f"unreadable: {type(e).__name__}: {str(e).replace(str(src), src.name)}")

    try:
        if doc.needs_pass:
            return done("skipped", "encrypted")
        rec["repaired"] = bool(doc.is_repaired)
        if skip_repaired and doc.is_repaired:
            return done("skipped", "repaired_on_open")
        rec["pages"] = doc.page_count
        meta = doc.metadata or {}
        rec["producer"] = meta.get("producer") or None
        rec["creator"] = meta.get("creator") or None
        if doc.page_count == 0:
            return done("skipped", "no_pages")

        toc = doc.get_toc(simple=True)
        rec["outline_entries"] = len(toc)
        valid = [(lvl, p) for lvl, _, p in toc if 1 <= p <= doc.page_count]
        valid_entries = len(valid)
        if valid_entries == 0:
            return done("skipped", "no_outline")
        rec["outline_depth"] = max(lvl for lvl, _ in valid)
        if valid_entries == 1:
            # a single bookmark is a title, not a table of contents
            return done("skipped", "single_entry_outline")
        level, chapters = choose_split_level(toc, doc.page_count)
        rec["split_level"] = level
        rec["chapters"] = len(chapters)

        why = sanity_check(chapters, doc.page_count)
        if why:
            return done("skipped", f"suspect_outline: {why}")

        sections = build_sections(chapters, doc.page_count)
        names = dedupe_names([chapter_filename(book, clean_title(t)) for t, _, _ in sections])

        if dry_run:
            return done("would_split")

        final_dir = dst_root / book
        tmp_dir = dst_root / f".tmp-{book}"
        if tmp_dir.exists():
            shutil.rmtree(tmp_dir)
        tmp_dir.mkdir(parents=True)

        written_pages = 0
        try:
            for (title, start, end), name in zip(sections, names):
                out = pymupdf.open()
                out.insert_pdf(doc, from_page=start - 1, to_page=end - 1)
                if title != FRONTMATTER_TITLE:
                    bm = nested_bookmarks(toc, level, start, end)
                    if bm:
                        try:
                            out.set_toc(bm)
                        except Exception:  # noqa: BLE001
                            pass
                # keep the source's page numbering: page 294 of the book is still "294" here
                out.set_page_labels([{"startpage": 0, "prefix": "", "style": "D", "firstpagenum": start}])
                out.set_metadata({**{k: v for k, v in meta.items() if v}, "title": clean_title(title)})
                out.save(tmp_dir / name, garbage=1)
                written_pages += out.page_count
                out.close()
            if written_pages != doc.page_count:
                raise RuntimeError(f"page count mismatch: wrote {written_pages}, source has {doc.page_count}")
            if final_dir.exists():
                shutil.rmtree(final_dir)
            tmp_dir.rename(final_dir)
        except Exception as e:  # noqa: BLE001
            shutil.rmtree(tmp_dir, ignore_errors=True)
            return done("error", f"{type(e).__name__}: {e}")
        return done("split")
    finally:
        doc.close()


# ---------------------------------------------------------------------------
# Driver
# ---------------------------------------------------------------------------

def list_pdfs(src: Path) -> list[Path]:
    return sorted(
        p for p in src.iterdir()
        if p.is_file() and p.suffix.lower() == ".pdf" and not p.name.startswith((".", "._"))
    )


def load_log(log_path: Path) -> dict[str, str]:
    """book -> last outcome recorded."""
    seen: dict[str, str] = {}
    if log_path.exists():
        with log_path.open(encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    r = json.loads(line)
                    seen[r["book"]] = r["outcome"]
                except (ValueError, KeyError):
                    continue
    return seen


def write_summary(log_path: Path, summary_path: Path, elapsed: float | None = None):
    outcomes: Counter = Counter()
    reasons: Counter = Counter()
    chapters_total = 0
    seen: dict[str, dict] = {}
    if log_path.exists():
        with log_path.open(encoding="utf-8") as f:
            for line in f:
                try:
                    r = json.loads(line)
                    seen[r["book"]] = r  # last record per book wins
                except (ValueError, KeyError):
                    continue
    repaired = 0
    nested = 0
    for r in seen.values():
        outcomes[r["outcome"]] += 1
        repaired += 1 if r.get("repaired") else 0
        if r["outcome"] in ("split", "would_split"):
            chapters_total += r.get("chapters") or 0
            if (r.get("outline_depth") or 0) > (r.get("split_level") or 0):
                nested += 1
        if r["outcome"] in ("skipped", "error"):
            reasons[(r["outcome"], (r.get("reason") or "").split(":")[0])] += 1
    lines = [f"split_library.py summary  ({datetime.now().isoformat(timespec='seconds')})", ""]
    lines.append(f"files examined: {len(seen)}")
    for k, v in sorted(outcomes.items()):
        lines.append(f"  {k:<13}{v:>7}")
    if chapters_total:
        lines.append(f"  chapter files written: {chapters_total}")
        lines.append(f"  books with bookmarks nested below the chapter level: {nested}")
    if repaired:
        lines.append(f"  files whose index needed repair on open: {repaired}")
    if reasons:
        lines.append("")
        lines.append("skipped / error by reason:")
        for (oc, reason), v in sorted(reasons.items(), key=lambda kv: -kv[1]):
            lines.append(f"  {oc:<8}{reason:<32}{v:>7}")
    if elapsed is not None:
        lines.append("")
        lines.append(f"this run: {elapsed:.1f} s")
    text = "\n".join(lines) + "\n"
    summary_path.write_text(text, encoding="utf-8")
    return text


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("src", type=Path, help="flat directory of PDFs (read-only)")
    ap.add_argument("dst", type=Path, nargs="?", help="output directory (default: sibling '<SRC>-split')")
    ap.add_argument("--dry-run", action="store_true",
                    help="examine every file and log what would happen; write no PDFs")
    ap.add_argument("--limit", type=int, metavar="N", help="process a random sample of N files")
    ap.add_argument("--workers", type=int, default=max(1, (os.cpu_count() or 2) - 1),
                    help="parallel worker processes (default: CPU count - 1)")
    ap.add_argument("--retry", action="store_true",
                    help="re-examine files previously logged as skipped or error")
    ap.add_argument("--include-repaired", action="store_true",
                    help="also split files whose index PyMuPDF had to repair on open "
                         "(default: log them as skipped/repaired_on_open for a later pass)")
    ap.add_argument("--seed", type=int, default=None, help="random seed for --limit")
    a = ap.parse_args(argv)

    src = a.src.expanduser().resolve()
    if not src.is_dir():
        ap.error(f"not a directory: {src}")
    dst = (a.dst.expanduser().resolve() if a.dst else src.parent / f"{src.name}-split")
    if dst == src or src in dst.parents:
        ap.error("output directory must not be inside the input directory")
    log_dir = dst / "_log"
    log_dir.mkdir(parents=True, exist_ok=True)
    log_path = log_dir / ("dry-run.jsonl" if a.dry_run else "results.jsonl")
    summary_path = log_dir / ("dry-run-summary.txt" if a.dry_run else "summary.txt")

    # leftover temp folders from an interrupted run
    for p in dst.glob(".tmp-*"):
        if p.is_dir():
            shutil.rmtree(p, ignore_errors=True)

    all_pdfs = list_pdfs(src)
    logged = load_log(log_path)
    todo = []
    for p in all_pdfs:
        book = p.stem
        if not a.dry_run and (dst / book).is_dir():
            continue  # already split; delete the folder to redo it
        prev = logged.get(book)
        if a.dry_run:
            if prev is None or a.retry:
                todo.append(p)
        elif prev is None or prev == "split" or a.retry:
            # prev == "split" with no folder: the folder was deleted, so redo it
            todo.append(p)
    if a.limit is not None and a.limit < len(todo):
        rng = random.Random(a.seed)
        todo = sorted(rng.sample(todo, a.limit))

    print(f"{len(all_pdfs)} PDFs in {src}; {len(todo)} to process"
          f"{' (dry run)' if a.dry_run else ''}; output in {dst}", file=sys.stderr)
    if not todo:
        print(write_summary(log_path, summary_path), file=sys.stderr)
        return 0

    jobs = [(str(p), str(dst), a.dry_run, not a.include_repaired) for p in todo]
    counts: Counter = Counter()
    t0 = time.monotonic()
    width = len(str(len(jobs)))

    def handle(i, rec, log_f):
        log_f.write(json.dumps(rec, ensure_ascii=False) + "\n")
        log_f.flush()
        counts[rec["outcome"]] += 1
        extra = f" ({rec['chapters']} chapters)" if rec["outcome"] in ("split", "would_split") else \
                f" ({rec['reason']})" if rec.get("reason") else ""
        print(f"[{i:>{width}}/{len(jobs)}] {rec['outcome']:<12}{rec['book']}{extra}", file=sys.stderr)

    with log_path.open("a", encoding="utf-8") as log_f:
        try:
            if a.workers <= 1:
                for i, job in enumerate(jobs, 1):
                    handle(i, process_file(job), log_f)
            else:
                with mp.Pool(processes=a.workers, maxtasksperchild=50) as pool:
                    for i, rec in enumerate(pool.imap_unordered(process_file, jobs), 1):
                        handle(i, rec, log_f)
        except KeyboardInterrupt:
            print("\ninterrupted; progress so far is logged, rerun to resume", file=sys.stderr)

    elapsed = time.monotonic() - t0
    print("", file=sys.stderr)
    print(write_summary(log_path, summary_path, elapsed), file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
