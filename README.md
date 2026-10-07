# pdf-split

Split a flat directory of PDFs into one PDF per chapter, using each file's
embedded outline (bookmarks). One script, one dependency.

A file is split **only** if it carries bookmarks that look like chapters.
Everything else is left alone and logged with a reason. No OCR, no parsing of
printed tables of contents, no manual review step.

## Install

```
pip install pymupdf
```

Python 3.9+. Runs on macOS, Linux, Windows.

## Use

```
# 1. size the job first: examines every file, writes nothing, takes minutes
python split_library.py /path/to/my-pdf-library --dry-run

# 2. try a sample for real
python split_library.py /path/to/my-pdf-library --limit 20

# 3. the whole library (interrupt and rerun freely; it resumes)
python split_library.py /path/to/my-pdf-library
```

Output goes to a sibling directory `my-pdf-library-split/` unless you give a
second path. The input directory is never modified.

```
my-pdf-library-split/
  _log/
    results.jsonl        one line per source file examined
    dry-run.jsonl        same, from --dry-run (kept separate)
  example/
    example - [frontmatter].pdf    pages before the first chapter, if any
    example - Introduction.pdf
    example - Chapter 1.pdf
```

Options:

| Flag | Meaning |
|---|---|
| `--dry-run` | Examine and log every file; write no PDFs. Logs to `dry-run.jsonl`. |
| `--limit N` | Random sample of N files. |
| `--workers N` | Parallel processes. Default: CPU count minus one. |
| `--retry` | Re-examine files already in the log. Books that have an output folder are still left alone. |
| `--include-repaired` | Also split files whose index MuPDF had to repair on open. By default these are logged as `repaired_on_open` and left for a later pass. |

## How a file is judged

1. Open it. PyMuPDF reads only the cross-reference table and catalog on open,
   not the pages, so this is cheap even for a 100 MB scan.
2. Unreadable (including zero pages) or encrypted → skipped.
3. Read the outline. No entries pointing at a valid page → `no_outline`.
   Exactly one entry → `single_entry_outline` (a lone bookmark is a title,
   not a table of contents; common in single-chapter PDFs).
4. Chapter level = the shallowest outline level with at least two distinct
   start pages. This skips a single root bookmark that wraps the whole book.
   Note: if a book's top level is "Part I / Part II", it is split by part.
5. Sanity checks on the chapters at that level. Any failure → `suspect_outline`:
   - `single_chapter`: fewer than 2 chapters
   - `too_many_chapters`: more chapters than half the page count
   - `one_chapter_dominates`: one chapter is more than 95% of the pages
   
   Thresholds are constants at the top of the script.
6. Write each chapter into `.tmp-<book>/`, verify the chapter page counts add
   up exactly to the source's page count, then rename the folder into place.
   An interrupted run never leaves a half-written book.

Chapter files keep the bookmarks nested under their chapter (re-based to the
new file) and get the chapter title as their PDF metadata title. Pages are
never renumbered: each page in a chapter file shows the same number the viewer
showed for it in the source. For a source without page labels (most files)
that is its position, so a chapter cut from pages 294–305 opens showing
"294". For a source with its own labels (roman front matter, say) those labels
are carried over unchanged. Printed page numbers on the pages play no part. Pages before
the first chapter become `<book> - [frontmatter].pdf`.

## Naming

`<book> - <chapter title>.pdf`, where book is the source filename without
`.pdf`. Titles have `/ \ : * ? " < > |` and control characters replaced by
`-`, whitespace collapsed, trailing dots stripped, Unicode normalized to NFC,
and are truncated so the filename fits in 255 bytes. Duplicate titles within a
book get ` (2)`, ` (3)`, … in page order.

## Running in passes

```
# pass 1: clean files with usable outlines
python split_library.py LIB

# pass 2: files MuPDF had to repair on open; split if the recovered outline passes the checks
python split_library.py LIB --retry --include-repaired

# pass 3: no_outline and suspect_outline files remain in the log for a future tool
```

Books split in pass 1 have output folders, so pass 2 never reopens them.

## Resuming and redoing

- A book whose output folder exists is done and is never touched again.
  To redo one, delete its folder and run with `--retry`.
- A file already in `results.jsonl` was examined and is not reopened unless
  `--retry` is given.
- Leftover `.tmp-*` folders from an interrupted run are removed at startup.
- A count of outcomes is printed when the run ends. For anything more, query
  the log.

## The log

Each line of `results.jsonl`:

| Field | Meaning |
|---|---|
| `book` | source filename without `.pdf` |
| `source` | absolute path |
| `outcome` | `split`, `would_split` (dry run), `skipped`, `error` |
| `reason` | for skipped/error: `no_outline`, `single_entry_outline`, `encrypted`, `unreadable: …`, `suspect_outline: <check>`, `repaired_on_open`, or the exception |
| `pages` | page count (null if the file never opened) |
| `outline_entries` | raw bookmark count |
| `outline` | every bookmark as `{"level", "title", "page"}`, in document order; `page` is the 1-based PDF page, or -1 for a bookmark with no destination |
| `outline_depth` | deepest outline level that points at a real page (1 = flat). Greater than `split_level` means the book had sub-chapter bookmarks, which are carried into the chapter files |
| `has_page_labels` | whether the source defines its own page labels |
| `split_level` | outline level used as chapters |
| `chapters` | number of chapters found at that level |
| `repaired` | whether MuPDF repaired the file's index on open |
| `producer`, `creator` | PDF metadata; useful for spotting which tool or publisher produced the problem files |
| `bookmarks_dropped` | titles of chapter files written without their nested bookmarks, because a sub-bookmark points at a page in a different chapter from its parent, leaving an entry with no parent in the chapter file, which PyMuPDF refuses to write. Normally `[]` |
| `timestamp` | when the file was examined (UTC) |

To see what's in the skipped pile:

```
jq -r 'select(.outcome=="skipped") | [.reason, .producer, .book] | @tsv' \
    my-pdf-library-split/_log/results.jsonl | sort | less
```

## Tests

`tests/make_fixtures.py OUTDIR` writes a small synthetic library covering
good outlines, nested bookmarks, a root wrapper bookmark, no outline, scanned
pages with an outline, encryption, the three suspect cases, nasty titles, a
truncated file, a non-PDF, and files that must be ignored. Run the script on
it and check the summary.

## Not done, on purpose

Books with no usable outline are only logged. Splitting those by their
printed table of contents, or OCR, would be a separate tool that reads this
log as its input.
