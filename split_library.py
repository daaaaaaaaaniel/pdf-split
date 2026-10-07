#!/usr/bin/env python3
"""Split a flat directory of PDFs into one PDF per chapter, using each file's
embedded outline (bookmarks). Files without a usable outline are left alone and
logged with a reason. Requires PyMuPDF (pip install pymupdf).

    python split_library.py SRC [DST] [--dry-run] [--limit N] [--workers N]
                                      [--retry] [--include-repaired]
"""
import argparse
import json
import multiprocessing
import os
import random
import re
import shutil
import sys
import unicodedata
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

import pymupdf

# An outline is treated as chapters only if it passes these three checks.
MIN_CHAPTERS = 2
MAX_CHAPTERS_PER_PAGE = 0.50   # more chapters than half the page count: suspect
MAX_CHAPTER_SHARE = 0.95       # one chapter covering more than this: suspect

FRONTMATTER = "[frontmatter]"


def clean_title(title):
    t = unicodedata.normalize("NFC", title or "")
    t = re.sub(r'[\\/:*?"<>|\x00-\x1f\x7f]', "-", t)
    t = re.sub(r"\s+", " ", t).strip().rstrip(".").strip()
    return t or "untitled"


def filenames(book, titles):
    """'<book> - <title>.pdf' for each title, truncated to 255 bytes, duplicates numbered."""
    out, seen = [], Counter()
    for t in titles:
        budget = 255 - len(f"{book} - .pdf".encode())
        while len(t.encode()) > budget:
            t = t[:-1].rstrip()
        seen[t.casefold()] += 1
        n = seen[t.casefold()]
        out.append(f"{book} - {t}{f' ({n})' if n > 1 else ''}.pdf")
    return out


def chapters_from_outline(toc, page_count):
    """Pick the chapter level: the shallowest outline level with 2+ distinct start
    pages (this skips a single root bookmark). Returns (level, [(page, title)...])."""
    valid = [(lvl, title, page) for lvl, title, page in toc if 1 <= page <= page_count]
    levels = sorted({lvl for lvl, _, _ in valid})
    level = next((l for l in levels if len({p for lv, _, p in valid if lv == l}) >= 2), levels[0])
    by_page = {}
    for lvl, title, page in valid:
        if lvl == level:
            by_page.setdefault(page, title)
    return level, sorted(by_page.items())


def suspect(chapters, page_count):
    starts = [p for p, _ in chapters] + [page_count + 1]
    if len(chapters) < MIN_CHAPTERS:
        return "single_chapter"
    if len(chapters) > page_count * MAX_CHAPTERS_PER_PAGE:
        return "too_many_chapters"
    if max(b - a for a, b in zip(starts, starts[1:])) > page_count * MAX_CHAPTER_SHARE:
        return "one_chapter_dominates"
    return None


def page_labels(doc, start, end):
    """Labels for a chapter file covering source pages start..end, so each page shows
    exactly what the source's viewer showed: the source's own labels if it has any,
    otherwise the page's position in the source."""
    if doc.get_page_labels():
        return [{"startpage": i, "prefix": doc[start - 1 + i].get_label(), "style": ""}
                for i in range(end - start + 1)]
    return [{"startpage": 0, "style": "D", "firstpagenum": start}]


def process(job):
    """Examine one PDF and, unless dry_run, split it. Returns the log record."""
    src, dst, dry_run, include_repaired = job
    src, dst = Path(src), Path(dst)
    book = src.stem
    rec = {"book": book, "source": str(src), "outcome": None, "reason": None,
           "pages": None, "outline_entries": None, "outline_depth": None,
           "has_page_labels": None, "split_level": None, "chapters": None,
           "repaired": None, "producer": None, "creator": None,
           "timestamp": datetime.now(timezone.utc).isoformat(timespec="seconds")}

    def done(outcome, reason=None):
        rec.update(outcome=outcome, reason=reason)
        return rec

    try:
        doc = pymupdf.open(src)
        if doc.page_count == 0:
            raise ValueError("no pages")
    except Exception as e:  # noqa: BLE001
        return done("skipped", f"unreadable: {type(e).__name__}: {str(e).replace(str(src), src.name)}")

    with doc:
        if doc.needs_pass:
            return done("skipped", "encrypted")
        meta = doc.metadata or {}
        rec.update(pages=doc.page_count, repaired=bool(doc.is_repaired),
                   producer=meta.get("producer") or None, creator=meta.get("creator") or None)
        if doc.is_repaired and not include_repaired:
            return done("skipped", "repaired_on_open")

        toc = doc.get_toc(simple=True)
        valid = [(lvl, p) for lvl, _, p in toc if 1 <= p <= doc.page_count]
        rec.update(outline_entries=len(toc), outline_depth=max((lvl for lvl, _ in valid), default=None))
        if not valid:
            return done("skipped", "no_outline")
        if len(valid) == 1:
            return done("skipped", "single_entry_outline")

        level, chapters = chapters_from_outline(toc, doc.page_count)
        rec.update(split_level=level, chapters=len(chapters), has_page_labels=bool(doc.get_page_labels()))
        why = suspect(chapters, doc.page_count)
        if why:
            return done("skipped", f"suspect_outline: {why}")
        if dry_run:
            return done("would_split")

        # sections: (title, first page, last page), 1-based inclusive
        starts = [p for p, _ in chapters]
        sections = [(t, p, (starts[i + 1] - 1 if i + 1 < len(starts) else doc.page_count))
                    for i, (p, t) in enumerate(chapters)]
        if starts[0] > 1:
            sections.insert(0, (FRONTMATTER, 1, starts[0] - 1))
        names = filenames(book, [clean_title(t) for t, _, _ in sections])

        tmp, final = dst / f".tmp-{book}", dst / book
        shutil.rmtree(tmp, ignore_errors=True)
        tmp.mkdir(parents=True)
        try:
            written = 0
            for (title, start, end), name in zip(sections, names):
                out = pymupdf.open()
                out.insert_pdf(doc, from_page=start - 1, to_page=end - 1)
                if title != FRONTMATTER:
                    nested = [[lvl - level, re.sub(r"\s+", " ", t).strip(), p - start + 1]
                              for lvl, t, p in toc if lvl > level and start <= p <= end]
                    try:
                        out.set_toc(nested)
                    except Exception:  # noqa: BLE001  (irregular hierarchy: chapter file gets no bookmarks)
                        pass
                out.set_page_labels(page_labels(doc, start, end))
                # the chapter file carries the source's metadata unchanged (title, author, ...);
                # only its creation date is its own
                out.set_metadata({**meta, "creationDate": pymupdf.get_pdf_now()})
                out.save(tmp / name, garbage=1)
                written += out.page_count
                out.close()
            if written != doc.page_count:
                raise RuntimeError(f"page count mismatch: wrote {written}, source has {doc.page_count}")
            shutil.rmtree(final, ignore_errors=True)
            tmp.rename(final)
        except Exception as e:  # noqa: BLE001
            shutil.rmtree(tmp, ignore_errors=True)
            return done("error", f"{type(e).__name__}: {e}")
        return done("split")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("src", type=Path, help="flat directory of PDFs (never modified)")
    ap.add_argument("dst", type=Path, nargs="?", help="output directory (default: sibling '<SRC>-split')")
    ap.add_argument("--dry-run", action="store_true", help="examine and log every file; write no PDFs")
    ap.add_argument("--limit", type=int, metavar="N", help="process a random sample of N files")
    ap.add_argument("--workers", type=int, default=max(1, (os.cpu_count() or 2) - 1), help="parallel processes")
    ap.add_argument("--retry", action="store_true", help="re-examine files already in the log")
    ap.add_argument("--include-repaired", action="store_true",
                    help="also split files whose index MuPDF had to repair (default: skip them for a later pass)")
    a = ap.parse_args()

    src = a.src.expanduser().resolve()
    dst = a.dst.expanduser().resolve() if a.dst else src.parent / f"{src.name}-split"
    if not src.is_dir() or dst == src or src in dst.parents:
        ap.error("SRC must be a directory and DST must not be inside it")
    log = dst / "_log" / ("dry-run.jsonl" if a.dry_run else "results.jsonl")
    log.parent.mkdir(parents=True, exist_ok=True)
    for leftover in dst.glob(".tmp-*"):
        shutil.rmtree(leftover, ignore_errors=True)

    logged = set()
    if log.exists() and not a.retry:
        logged = {json.loads(line)["book"] for line in log.open(encoding="utf-8") if line.strip()}
    todo = [p for p in sorted(src.iterdir())
            if p.is_file() and p.suffix.lower() == ".pdf" and not p.name.startswith(".")
            and p.stem not in logged and not (dst / p.stem).is_dir()]
    if a.limit and a.limit < len(todo):
        todo = sorted(random.sample(todo, a.limit))
    print(f"{len(todo)} files to examine{' (dry run)' if a.dry_run else ''}; output in {dst}", file=sys.stderr)

    jobs = [(str(p), str(dst), a.dry_run, a.include_repaired) for p in todo]
    counts = Counter()
    with log.open("a", encoding="utf-8") as f, multiprocessing.Pool(a.workers, maxtasksperchild=50) as pool:
        for i, rec in enumerate(pool.imap_unordered(process, jobs), 1):
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
            f.flush()
            counts[rec["reason"].split(":")[0] if rec["reason"] else rec["outcome"]] += 1
            detail = f"{rec['chapters']} chapters" if rec["outcome"] in ("split", "would_split") else rec["reason"]
            print(f"[{i}/{len(jobs)}] {rec['outcome']:<12}{rec['book']}  ({detail})", file=sys.stderr)
    for k, n in counts.most_common():
        print(f"{n:>6}  {k}", file=sys.stderr)


if __name__ == "__main__":
    main()
