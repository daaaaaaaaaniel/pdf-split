# pdf-split

Split a flat directory of PDFs into one folder per book, with one PDF per
chapter, using each file's embedded outline (bookmarks). Files without usable
bookmarks are left alone and logged with a reason. One script, one dependency.

```
pip install pymupdf
python split_library.py /path/to/library --dry-run    # examine, write nothing
python split_library.py /path/to/library              # split; resumable
```

Output goes to `library-split/` next to the input, as

```
library-split/
  _log/results.jsonl                     one line per file examined
  example/
    example - [frontmatter].pdf
    example - Introduction.pdf
    example - Chapter 1.pdf
```

Three rules the script never breaks: the source directory is never modified;
the outline is the authority on how a file is split; pages are never
renumbered.

**[MANUAL.md](MANUAL.md)** has everything else: every flag, how a file is
judged, what a chapter file contains, the log's fields and how to query it,
running in passes, resuming, what each outcome means and what to do about it,
tuning, limitations, tests, glossary.
