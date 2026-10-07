#!/usr/bin/env python3
"""Generate a small library of synthetic PDFs covering the cases split_library.py must handle.

    python tests/make_fixtures.py OUTDIR
"""
import sys
from pathlib import Path

import pymupdf


def make(path: Path, pages: int, toc=None, encrypt=False, text=True):
    doc = pymupdf.open()
    for i in range(1, pages + 1):
        page = doc.new_page()
        if text:
            page.insert_text((72, 72), f"{path.stem} - page {i}", fontsize=14)
    if toc:
        doc.set_toc(toc)
    kw = {}
    if encrypt:
        kw = dict(encryption=pymupdf.PDF_ENCRYPT_AES_256, user_pw="secret", owner_pw="secret")
    doc.save(path, **kw)
    doc.close()


def main(out: Path):
    out.mkdir(parents=True, exist_ok=True)

    # 1. plain good outline, chapters start on page 1 -> no frontmatter
    make(out / "plain book.pdf", 30, [
        [1, "Introduction", 1], [1, "Chapter 1", 6], [1, "Chapter 2", 16], [1, "Conclusion", 26]])

    # 2. frontmatter + nested bookmarks + one bookmark with no destination (-1)
    make(out / "nested book.pdf", 40, [
        [1, "Preface", 4],
        [1, "Chapter 1: Beginnings", 8], [2, "1.1 Early", 8], [2, "1.2 Later", 12], [3, "1.2.1 Detail", 14],
        [1, "Chapter 2: Middles", 20], [2, "2.1 Stuff", 24],
        [1, "Chapter 3: Ends", 30],
        [1, "Index", -1]])

    # 3. single root bookmark wrapping the book; real chapters at level 2
    make(out / "root wrapped.pdf", 25, [
        [1, "The Whole Book", 1],
        [2, "Part One", 1], [3, "Ch 1", 1], [3, "Ch 2", 6],
        [2, "Part Two", 13], [3, "Ch 3", 13], [3, "Ch 4", 19]])

    # 4. no outline at all
    make(out / "no outline.pdf", 12)

    # 5. scanned (no text) but with a good outline -> should still split
    make(out / "scanned with outline.pdf", 20, [[1, "A", 1], [1, "B", 11]], text=False)

    # 6. encrypted
    make(out / "encrypted.pdf", 10, [[1, "A", 1], [1, "B", 5]], encrypt=True)

    # 6b. outline with exactly one bookmark -> single_entry_outline
    make(out / "one bookmark.pdf", 18, [[1, "The Article Title", 1]])

    # 7. outline with a single chapter -> suspect
    make(out / "one chapter.pdf", 15, [[1, "Everything", 1], [1, "Also everything", 1]])

    # 8. a bookmark per page -> too many chapters
    make(out / "bookmark every page.pdf", 10, [[1, f"p{i}", i] for i in range(1, 11)])

    # 9. one giant chapter and a tiny appendix -> dominates
    make(out / "dominant chapter.pdf", 100, [[1, "Body", 1], [1, "Appendix", 99]])

    # 10. nasty characters, duplicate titles, very long title, trailing dots
    long_title = "A very long chapter title " * 15
    make(out / "nasty titles.pdf", 20, [
        [1, 'Intro: "quotes" / slashes \\ and <angles>?', 1],
        [1, "Repeat", 5], [1, "Repeat", 9], [1, "repeat", 12],
        [1, long_title, 15], [1, "Ends with dots...", 18]])

    # 10b. source with its own page labels (roman, arabic restarting, prefixed appendix)
    #      -> chapter files must show exactly the same labels
    doc = pymupdf.open()
    for i in range(30):
        doc.new_page().insert_text((72, 72), f"pos {i + 1}", fontsize=14)
    doc.set_toc([[1, "Preface", 3], [1, "Chapter 1", 9], [1, "Chapter 2", 18], [1, "Appendix", 25]])
    doc.set_page_labels([{"startpage": 0, "style": "r"},
                         {"startpage": 8, "style": "D", "firstpagenum": 1},
                         {"startpage": 24, "prefix": "A-", "style": "D", "firstpagenum": 1}])
    doc.save(out / "labelled book.pdf")
    doc.close()

    # 10c. a sub-bookmark whose page lies in a later chapter than its parent: inside
    #      Chapter 2 the first nested entry is then a grandchild with no parent, which
    #      PyMuPDF refuses -> Chapter 2 gets no bookmarks, listed in bookmarks_dropped
    make(out / "stray bookmark.pdf", 20, [
        [1, "Chapter 1", 1], [2, "1.1", 3], [3, "1.1.1 points into chapter 2", 12],
        [1, "Chapter 2", 10], [2, "2.1 Fine", 14]])

    # 11. corrupt file (truncated)
    good = out / "plain book.pdf"
    data = good.read_bytes()
    (out / "truncated.pdf").write_bytes(data[: len(data) // 2])

    # 12. not a PDF at all
    (out / "garbage.pdf").write_bytes(b"this is not a pdf\n" * 50)

    # 13. things that must be ignored
    (out / "._plain book.pdf").write_bytes(b"\x00\x05\x16\x07")
    (out / "notes.txt").write_text("ignore me")
    (out / "subdir").mkdir(exist_ok=True)
    make(out / "subdir" / "inside subdir.pdf", 5, [[1, "A", 1], [1, "B", 3]])

    # 14. uppercase extension
    make(out / "UPPER.PDF", 8, [[1, "One", 1], [1, "Two", 5]])

    print(f"fixtures written to {out}")


if __name__ == "__main__":
    main(Path(sys.argv[1]))
