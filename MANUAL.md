# split_library.py — Manual

One script that turns a flat folder of PDFs into one folder per book, with one
PDF per chapter, using the bookmarks ("outline") embedded in each PDF. Files
without usable bookmarks are left alone and logged with a reason.

This manual is written for a person running the script and for an agent
maintaining it. Section 1 states the rules that must survive any change.

## 1. Principles

These are not features; they are the contract. Any change to the script must
keep all three.

1. **The source directory is never modified.** The script opens source PDFs
   read-only and writes only inside the output directory. No source file is
   moved, renamed, repaired on disk, or touched in any way. Modification times
   in the source directory are identical before and after a run.
2. **The outline is the authority.** A PDF is split into whatever its top
   usable bookmark level says: chapters of a book, parts of a collection,
   sections of an article. The script never reads bookmark titles to second-
   guess this, never uses printed tables of contents, and never OCRs. A file
   whose outline does not pass the checks in section 5 is skipped, not guessed
   at.
3. **No renumbering.** Every page in a chapter file shows the same page number
   the viewer showed for it in the source. Metadata (title, author, subject,
   keywords, dates) is carried over unchanged; only the chapter file's creation
   date is its own.

## 2. Requirements and installation

- Python 3.9 or later. On macOS the command is `python3`, never `python`.
- PyMuPDF, installed into that same Python: `python3 -m pip install pymupdf`.
  Nothing else. (A `brew install pymupdf` goes into Homebrew's own Python,
  `/opt/homebrew/bin/python3`, which may not be the `python3` your shell finds
  first. `python3 -c "import pymupdf"` tells you whether the two line up.)
- macOS, Linux or Windows. Local disk for both input and output; not a
  cloud-synced folder.
- Free space: the output is somewhat larger than the input, because fonts and
  images shared between chapters are copied into every chapter file.

## 3. Quick start

```
# 1. Examine every file, write nothing. Minutes, not hours.
python3 split_library.py /path/to/library --dry-run

# 2. Split a random sample for real and open the results.
python3 split_library.py /path/to/library --limit 20

# 3. Split everything.
python3 split_library.py /path/to/library
```

The full run can be interrupted at any point (Ctrl-C, or the machine going
to sleep) and continued later by running the same command again: the script
skips every file it has already dealt with and carries on with the rest.
Nothing needs to be passed or cleaned up by hand. Section 10 has the details.

Where the output goes: with no second argument, the script creates a new
folder next to the library, named after it with `-split` added. So for
`/Volumes/X/my-library` it writes to `/Volumes/X/my-library-split`. To put
the output anywhere else, give that path as the second argument:

```
python3 split_library.py /Volumes/X/my-library /Volumes/Y/splits
```

To change the default itself, edit the `OUTPUT_DIR` constant at the top of
the script (section 12).

## 4. Command reference

```
python3 split_library.py SRC [DST] [--dry-run] [--limit N] [--workers N]
                                  [--level N] [--retry] [--include-repaired]
```

| Argument | Meaning |
|---|---|
| `SRC` | Flat directory of PDFs. Only top-level `*.pdf` files are considered (case-insensitive). Subfolders, hidden files and macOS `._` files are ignored and never logged. |
| `DST` | Output directory, created if needed. If omitted, the `OUTPUT_DIR` constant decides (section 12); as shipped, a folder next to `SRC` named `<SRC's name>-split`. Must not be `SRC` or inside it. |
| `--dry-run` | Run the whole decision tree but write no PDFs. Outcomes go to `_log/dry-run.jsonl` instead of `results.jsonl`, so a dry run never affects a real run's resume logic. |
| `--limit N` | Examine a random sample of N files from the work list. |
| `--workers N` | Parallel worker processes. Default: CPU count minus one. Each worker is recycled after 50 files. |
| `--level N` | Split at outline level N instead of choosing the level automatically: every bookmark at level 1 through N starts a file (section 6). A book with no bookmarks at level N is skipped as `level_not_present`. |
| `--retry` | Re-examine files already in the log. Books that have an output folder are still left alone. |
| `--include-repaired` | Also split files whose index MuPDF had to repair on open. By default these are logged `repaired_on_open` and left for a later pass. |

Progress is printed to stderr, one line per file, followed by a count of
outcomes. The log is the durable record.

## 5. How a file is judged

Checks run in this order; the first that fires decides the outcome. "Skipped"
always means: source untouched, nothing written, one line in the log.

1. **Does it open, with at least one page?** No → `skipped: unreadable`.
   PyMuPDF reads only the file's index and catalog on open, not the pages, so
   this is cheap even for a 100 MB scan.
2. **Does it need a password?** Yes → `skipped: encrypted`.
3. **Did MuPDF have to repair the index on open?** Yes, and `--include-repaired`
   not given → `skipped: repaired_on_open`. See glossary, "repair".
4. **Does any bookmark point at a real page?** No → `skipped: no_outline`.
5. **Is there exactly one such bookmark?** Yes → `skipped: single_entry_outline`.
   A lone bookmark is a title, not a table of contents.
6. **Choose the chapter level** (section 6) and the chapters at that level.
   With `--level N` and no bookmarks at level N → `skipped: level_not_present`.
7. **Sanity checks on the chapters.** Any failure → `skipped: suspect_outline: <check>`:
   - `single_chapter`: fewer than 2 chapters.
   - `too_many_chapters`: more chapters than half the page count (a bookmark
     per page or per figure).
   - `one_chapter_dominates`: one chapter covers more than 95% of the pages.
8. **`--dry-run`?** Yes → `would_split`, nothing written.
9. **Write** every chapter into `.tmp-<book>/` (section 7). If anything
   fails during writing (disk full, a filename the filesystem rejects, a page
   MuPDF cannot copy) → `error: <exception>`, temp folder discarded.
10. **Do the chapter page counts add up exactly to the source's?** No →
    `error: page count mismatch`, temp folder discarded. Yes → temp folder
    renamed to `<book>/`, outcome `split`.

## 6. Which bookmarks are chapters

The script uses only a bookmark's **level** (1 = top of the outline, 2 =
nested under that, …) and its **page**. Titles play no part.

**Step one — choose the chapter level.** Group the bookmarks that point at a
real page by level. Starting at the shallowest level, ask: does this level
have at least two *distinct* start pages? If yes, this is the chapter level.
If no, ask the next deeper level. If no level qualifies, the shallowest level
is used and the book will fail the `single_chapter` check.

The two-distinct-pages rule exists because many PDFs have a single top-level
bookmark wrapping the whole book, with the real chapters one level down. It
also means a book whose top level is Part I / Part II is split by part, with
the chapters nested inside each part file. That is by design.

**Step two — sort every bookmark against that level.**

| Bookmark | Becomes |
|---|---|
| Points outside the document, or nowhere (page −1) | Ignored. Still listed in the log's `outline` and in `_<book>.txt`. |
| Shallower than the chapter level | Dropped: it is a wrapper above the chapters and would point outside every chapter file. |
| Deeper than the chapter level | A nested bookmark inside the chapter file whose pages contain it. |
| At the chapter level | A chapter. Starts a new file at that page. |

**Same-page collisions.** When two or more chapter bookmarks point at the same
page, they become one file whose title is their titles joined with ` - `, in
document order: a part heading that sits on the same page as its first chapter
gives `PART I Questions - 1. Technics- An Introduction.pdf`. Nothing is
discarded. Every such group is recorded in the log's `merged_titles`.

The result is a page-ordered list of chapters. Pages before the first chapter
become a `[frontmatter]` section.

**With `--level N`.** The automatic choice is replaced by: every bookmark at
level 1 through N is a chapter. A part at level 1 whose first chapter at
level 2 starts on a later page gets a file of its own, covering the pages up
to that chapter (typically a title page); a part that starts on the same page
as its first chapter is merged with it as above. Bookmarks deeper than N are
nested inside files as usual. If the outline has nothing at level N, the book
is skipped as `level_not_present` rather than falling back to the automatic
choice. Note that `--level 1` is not the same as the default: the default
skips a single root bookmark, `--level 1` takes it literally.

## 7. What a chapter file contains

- **Pages**: the chapter's start page up to the page before the next chapter's
  start; the last chapter runs to the end of the document. Every source page
  lands in exactly one chapter file, which the page-count check enforces.
- **Page labels**: whatever the source's viewer showed for each page. If the
  source has its own labels (roman front matter, "Cover", "A-1"…), they are
  copied per page. If it has none, the viewer was showing positions, so a
  chapter cut from pages 294–305 opens showing "294".
- **Bookmarks**: the source's bookmarks nested under this chapter, re-based to
  the new file and shifted up so the chapter's children are top-level. The
  chapter's own bookmark is not included. If a nested bookmark has no parent
  in this file (its parent points into another chapter), PyMuPDF refuses the
  outline and the file is written with no bookmarks; the log records this in
  `bookmarks_dropped`.
- **Metadata**: the source's, unchanged, except `creationDate`, which is the
  time the chapter file was written.
- **Filename**: `<book> - <chapter title>.pdf`, where `<book>` is the source
  filename without `.pdf`. The title is cleaned: `/ \ : * ? " < > |` and
  control characters become `-`, whitespace is collapsed, leading and
  trailing spaces and trailing dots are stripped, Unicode is normalised to
  NFC. If the full filename would exceed 255 bytes, the title is truncated.
  Duplicate titles within a book get ` (2)`, ` (3)`… in page order. Leading
  pages become `<book> - [frontmatter].pdf`.
- **Folder**: `<DST>/<book>/`. Written first as `<DST>/.tmp-<book>/` and
  renamed only when complete, so an interrupted run never leaves a
  half-written book that looks finished.
- **`_<book>.txt`**: a plain-text tree of the source's entire outline, every
  level, in document order, with `[frontmatter]` first when it exists. Each
  entry is followed by ` · ` and the page label (or position, for a source
  without labels) of the page it points at, as the source's viewer shows it.
  Titles are verbatim apart from collapsed whitespace, so this file is where
  the exact titles survive the filename cleanup. A bookmark with no
  destination appears without a number.

  ```
  Stiegler_1998_-_Technics_and_Time_1
  ├── [frontmatter] · 1
  ├── Preface · 6
  ├── Part I: The Invention of the Human · 34
  │   ├── Introduction · 36
  │   └── 1: Theories of Technical Evolution · 44
  └── Notes · 294
  ```

## 8. The log

`<DST>/_log/results.jsonl` (or `dry-run.jsonl` for a dry run). One JSON
object per line, one line per file examined, appended as each file finishes.
It is both the record of what happened and the script's memory for resuming.

### 8.1 Fields

| Field | Type | Meaning |
|---|---|---|
| `book` | string | Source filename without `.pdf`. Also the output folder name. |
| `source` | string | Absolute path of the source PDF. |
| `outcome` | string | `split`, `would_split` (dry run only), `skipped`, or `error`. |
| `reason` | string or null | Why, for `skipped` and `error`. Null for `split`. Values: `unreadable: <exception>`, `encrypted`, `repaired_on_open`, `no_outline`, `single_entry_outline`, `suspect_outline: single_chapter`, `suspect_outline: too_many_chapters`, `suspect_outline: one_chapter_dominates`, `level_not_present`, `page count mismatch: …`, or the text of any other exception during writing. |
| `pages` | int or null | Page count. Null if the file never opened. |
| `outline_entries` | int or null | Total bookmarks, including ones with no destination. |
| `outline_depth` | int or null | Deepest level among bookmarks that point at a real page. 1 means a flat outline. Greater than `split_level` means the book had sub-chapter bookmarks. |
| `outline` | list | Every bookmark, in document order, as `{"level", "title", "page"}`. `page` is the 1-based PDF page, or −1 for a bookmark with no destination. Whitespace in titles is collapsed; nothing else is changed. |
| `has_page_labels` | bool or null | Whether the source defines its own page labels. Null if the file was skipped before this was checked. |
| `split_level` | int or null | The outline level used as chapters. |
| `chapters` | int or null | Number of chapters found at that level. |
| `repaired` | bool or null | Whether MuPDF had to rebuild the file's index on open. |
| `producer`, `creator` | string or null | PDF metadata naming the software that made the file. Problem files cluster by producer. |
| `level_forced` | bool | Whether `--level` was given for this run. |
| `merged_titles` | list of lists | Each group of chapter bookmarks that shared a start page and were joined into one file (section 6). Normally `[]`. |
| `bookmarks_dropped` | list | Titles of chapter files written without nested bookmarks (section 7). Normally `[]`. |
| `timestamp` | string | When the file was examined, UTC, ISO 8601. |

A file can appear more than once if it was re-examined with `--retry`. The
last line for a given `book` is the current state.

### 8.2 Reading it

Each line is self-contained, so `grep` works, but `jq` is the tool. All
examples assume you are in `<DST>/_log/`.

Count outcomes and reasons:

```
jq -r '[.outcome, (.reason // "" | split(":")[0])] | @tsv' results.jsonl | sort | uniq -c | sort -rn
```

List the books that were not split, with the reason:

```
jq -r 'select(.outcome != "split") | [.reason, .book] | @tsv' results.jsonl | sort
```

The pass-2 work list (files MuPDF repaired):

```
jq -r 'select(.reason == "repaired_on_open") | .book' results.jsonl
```

Which producers are responsible for the unsplittable files:

```
jq -r 'select(.outcome == "skipped") | .producer // "unknown"' results.jsonl | sort | uniq -c | sort -rn
```

Books whose top level is probably parts rather than chapters (few chapters,
many pages, deeper bookmarks present):

```
jq -r 'select(.outcome == "split" and .chapters <= 4 and .pages > 150 and .outline_depth > .split_level) | [.chapters, .pages, .book] | @tsv' results.jsonl
```

Everything the script saw in one book's outline, as an indented list:

```
jq -r 'select(.book == "Stiegler 2009 - Technics and Time, 2-1") | .outline[] | "\("  " * (.level - 1))\(.title)  p.\(.page)"' results.jsonl
```

Books where chapter bookmarks shared a page and their titles were joined:

```
jq -r 'select(.merged_titles | length > 0) | .book as $b | .merged_titles[] | [$b, join(" + ")] | @tsv' results.jsonl
```

Chapter files written without their bookmarks:

```
jq -r 'select(.bookmarks_dropped | length > 0) | [.book, (.bookmarks_dropped | join("; "))] | @tsv' results.jsonl
```

Books with the smallest page counts among those split, to see what the
outline-is-authority rule did to short documents:

```
jq -r 'select(.outcome == "split") | [.pages, .chapters, .book] | @tsv' results.jsonl | sort -n | head -40
```

The current state of every book, when some were re-examined:

```
jq -s 'group_by(.book) | map(last) | .[] | [.outcome, .book] | @tsv' -r results.jsonl
```

## 9. Running in passes

```
# Pass 1: clean files with usable outlines.
python3 split_library.py LIB

# Pass 2: files MuPDF had to repair. Split if the recovered outline passes the checks.
python3 split_library.py LIB --retry --include-repaired

# Pass 3: no_outline, single_entry_outline and suspect_outline files remain in
# the log for a separate, future tool (printed-TOC parsing or OCR). Not this script.
```

Books split in pass 1 have output folders, so pass 2 never reopens them.
"Repair" is of the file's index, not its outline: a repaired file whose
bookmarks were lost lands in `no_outline` in pass 2, like any other.

## 10. Resuming, redoing, interrupting

- **Interrupting**: Ctrl-C at any time. The log is flushed after every file.
  A book that was mid-write is left as `.tmp-<book>/`, which is deleted at
  the next start and the book is examined again.
- **Resuming**: rerun the same command. A book with an output folder is done.
  A file in the log was examined. Everything else is work. After an interrupt
  the two can differ by a book or two: a worker may have finished a folder in
  the instant before the main process stopped writing the log. Such a book is
  correctly treated as done, but has no log line.
- **Redoing one book**: delete its output folder, then run with `--retry`.
  (Without `--retry`, the log line keeps it skipped.)
- **Redoing everything**: delete `<DST>` and run again. Or delete only
  `_log/results.jsonl` to re-examine the skipped files while keeping the
  split books.
- **Re-examining skipped files after changing thresholds**: `--retry`. Split
  books are untouched.
- **Dry runs** never interact with any of this; they have their own log.

## 11. Outcomes: what they mean and what to do

| You see | It means | Do |
|---|---|---|
| `skipped: unreadable: …` | Not a PDF, or damaged beyond MuPDF's repair, or no pages, or a permissions problem. | Read the exception text. Usually nothing to do; the file is bad. |
| `skipped: encrypted` | Password-protected. | Remove the password with another tool if you have it; otherwise leave it. |
| `skipped: repaired_on_open` | Sloppy or damaged index, rebuilt in memory. The source file is unchanged. | Pass 2. |
| `skipped: no_outline` | No bookmarks. | Pass 3 material. Check `pages`: 20-page files are articles, 300-page ones are books. |
| `skipped: single_entry_outline` | One bookmark, a title. | Almost always a single-chapter PDF. Nothing to do. |
| `skipped: suspect_outline: single_chapter` | Several bookmarks, all on the same page. | Look at `outline` in the log. Usually an outline that was never finished. |
| `skipped: suspect_outline: too_many_chapters` | Bookmark per page, per figure, per paragraph. | Look at `outline`. If the entries are real chapters in a very short book, raise `MAX_CHAPTERS_PER_PAGE` and `--retry`. |
| `skipped: suspect_outline: one_chapter_dominates` | One bookmark covers nearly the whole book; the rest are cover, title page, index. | Look at `outline`. If the big one is "Text" or "Body", the outline describes the binding, not the contents. |
| `skipped: level_not_present` | `--level N` was given and this outline has nothing at level N. | Expected for flat outlines. Run those books without `--level`. |
| `error: page count mismatch` | A bug. Should never happen. | Report it, with the log line. |
| `error: <anything else>` | Writing failed: disk full, a filename the filesystem rejected, a page MuPDF could not copy. | Read the exception. Fix the cause and `--retry`. |
| `split` with `bookmarks_dropped` non-empty | Those chapter files have no bookmarks. | Cosmetic. Open the source's outline (in the log) if you want to see why. |
| `split`, but a chapter starts a few pages early or late | The source's bookmarks are wrong. The script followed them faithfully. | The only failure the script cannot detect. Fix the bookmarks in the source with another tool, delete the book's folder, `--retry`. |

## 12. Tuning

Four constants at the top of the script:

| Constant | Default | Effect |
|---|---|---|
| `OUTPUT_DIR` | `"../{name}-split"` | Where output goes when no `DST` is given on the command line. A relative path is resolved from inside `SRC`, so the default means "next to the library"; `{name}` is replaced by `SRC`'s folder name. An absolute path such as `"/Volumes/Y/splits"` is used as is. A `DST` on the command line always overrides it. |
| `MIN_CHAPTERS` | 2 | Fewer chapters than this → `single_chapter`. |
| `MAX_CHAPTERS_PER_PAGE` | 0.50 | More chapters than this fraction of the page count → `too_many_chapters`. |
| `MAX_CHAPTER_SHARE` | 0.95 | One chapter larger than this fraction of the book → `one_chapter_dominates`. |

After changing one, `--dry-run --retry` on the library shows what would
change before anything is written.

## 13. Known limitations

- **No per-file timeout.** A pathological PDF that hangs MuPDF stalls one
  worker forever; the others finish, the run does not. If a run seems stuck,
  Ctrl-C and look for the `.tmp-*` folder to identify the file.
- **The outline is trusted.** Bookmarks pointing at wrong pages produce a
  faithful split of the wrong pages. Nothing in the script can notice.
- **Parts split as parts by default.** A collection whose top level is
  Part I / Part II gives one file per part with chapters as bookmarks inside.
  `--level 2` gives chapter files instead; it applies to the whole run, so use
  it on a folder of such books or on a targeted `--retry`.
- **No OCR, no printed-TOC parsing.** Files without bookmarks are only logged.
- **Nested-bookmark edge case**: see `bookmarks_dropped`.
- **Filesystem limits**: filenames are kept under 255 bytes and free of the
  characters macOS, Windows and Linux forbid. Exotic filesystems may have
  other limits; those show up as `error` lines.

## 14. Tests

`tests/make_fixtures.py OUTDIR` writes a synthetic library of small PDFs, one
per case the script must handle: a plain outline, nested bookmarks, a root
wrapper bookmark, no outline, a single bookmark, scanned pages with an outline,
encryption, the three suspect cases, titles with forbidden characters and
duplicates, a source with its own page labels, a stray sub-bookmark, a book
with parts (for `--level`), a truncated file, a non-PDF, an uppercase
extension, and files that must be ignored. Run the script on it:

```
python3 tests/make_fixtures.py /tmp/lib
python3 split_library.py /tmp/lib /tmp/lib-split
```

Expected: 9 split, the rest skipped with the reason named in the fixture
file's comments, every split folder containing a `_<book>.txt` tree, `labelled book` showing the labels i–ii / iii–viii / 1–9 /
10–16 / A-1–A-6 across its five files, and `stray bookmark` logged with
`bookmarks_dropped: ["Chapter 2"]`. With `--level 2`, `parts book` gives
`Part I`, `Ch 1`, `Ch 2`, `Part II - Ch 3`, `Ch 4` and `[frontmatter]`, and
the flat books are skipped as `level_not_present`. Every split book's chapter
page counts add up to its source's.

## 15. Glossary

- **Outline / bookmarks / TOC.** The same thing: the clickable table of
  contents a PDF viewer shows in its sidebar, stored inside the PDF as a tree
  of entries, each with a title, a level and a page destination. Not the
  printed table of contents on a page, which the script never reads.
- **Level.** How deep an entry is nested in that tree. 1 is top-level.
- **Index (cross-reference table, xref).** A table at the end of a PDF saying
  at which byte each object starts. Readers use it to jump to a page or
  bookmark without reading the whole file.
- **Repair.** What MuPDF does when the index is wrong or missing: scan the
  whole file, find the objects, rebuild the index in memory. The file on disk
  is not changed. `doc.is_repaired` reports that it happened. Many producers
  write slightly sloppy indexes, so repaired files are often fine.
- **Position.** A page's place in the file, counting from 1. What the viewer
  shows when the PDF has no page labels.
- **Page label.** A per-page display string stored in the PDF ("iv", "A-1",
  "Cover") that the viewer shows instead of the position, and that "go to
  page" uses. Not the number printed on the page, which is just ink.
- **Producer / creator.** Metadata fields naming the software that wrote the
  PDF (producer) and the application the document came from (creator).
  Useful for grouping files that share a problem.
- **Chapter.** In this script, any bookmark at the chosen chapter level. The
  word is used loosely: it may be a part, a section, or an essay.
