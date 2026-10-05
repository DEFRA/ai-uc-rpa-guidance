# Decision log

Decisions taken while implementing features in this repo, newest section last. Each entry records the choice, the alternatives rejected, and why.

The frontend has its own log at `ai-uc-rpa-guidance-fe/DECISION_LOG.md`. Cross-repo decisions — notably the section-update API contract — are recorded here and referenced from there.

---

## Editing guidance sections (`markdown-editing-by-section`)

Goal: let editors correct imported guidance content without re-importing the Word document, and have corrections picked up by the publishing and review checkers.

### D1 — The API contract puts recomposition on the server

```
PUT /guidance/documents/{document_id}/sections/{section_number}
Body: { "heading": "<text, no number>", "markdown": "<body markdown, no heading line>" }
→ 204 | 404 (doc/section unknown) | 422 (validation)
```

The client sends the heading text and the body separately; the server composes the stored heading line from the manifest's `level` and `number`.

**Rejected:** accepting the whole section file (heading line included) and storing it verbatim. That lets the client change or corrupt the section number, which is a positional identity used as the S3 key, the manifest key and the intra-document anchor. Keeping composition server-side makes number drift impossible by construction.

### D2 — Section file format is derived from the parser, not re-invented

A section file is exactly:

```
{"#" * (level + 1)} {number} {heading}\n\n{body}
```

matching `app/guidance/pipeline/renderers/markdown.py:115-123` (`section_to_markdown`). Note the **`level + 1` offset** — a Word Heading 1 (`level == 1`) is stored as `##`, because `#` is reserved for the document title in `content.md`.

**Why it matters:** an edited section must be byte-compatible with a parsed one, or `content.md` assembly and heading-shift rendering in the frontend would behave differently for edited vs imported sections. A test drives a real `.docx` through the parser and asserts the write path produces the same bytes.

### D3 — `content.md` is regenerated on every section write

**Rejected:** leaving `content.md` stale and treating it as a build artefact of import only.

Not viable: the two checkers read *different* artefacts. Publishing assembles per-section files (`app/publishing/jobs/documents.py:92-107`) while review reads the monolithic `content.md` (`app/review/jobs/documents.py:88`). Skipping regeneration would leave review permanently analysing pre-edit text — a silent correctness bug, not untidiness.

Regeneration reuses `sectioning.fetch_joined_sections` (`app/guidance/documents/sectioning.py:46-60`) over every manifest number in document order, prefixed with `f"# {title}\n\n"`. That helper joins `rstrip("\n")`-ed parts with `"\n\n"` and adds one trailing newline, which is byte-identical to what the recursive renderer `to_markdown` (`markdown.py:105-112`) produces — so no second composition implementation exists to drift.

### D4 — Write logic lives in a `section_writer` module, not the router or `GuidanceService`

**Rejected — inline in the router:** the storage-backed GET routes take `s3_repository` directly and translate botocore errors inline, an idiom already repeated four times. A write touching four artefacts (section file, manifest, `content.md`, Mongo) would make that fifth copy substantially bigger.

**Rejected — `GuidanceService`:** it has no S3 dependency at all today; adding one to serve a single method would widen its constructor for every existing caller.

**Chosen:** a module beside `sectioning.py`, which is the established home for multi-step S3 work that takes `s3_repo` as a parameter.

### D5 — 404 is signalled by a typed exception

`SectionNotFoundError`, following `app/review/jobs/service.py:12 DocumentNotFoundError` and mapped in the router as `PUT /feedback/{feedback_id}` does (`app/feedback/router.py:185-226`).

**Rejected:** `GuidanceService`'s bare `ValueError` convention — untyped, and the rest of the codebase has moved away from it.

The 404 is decided by the section number's absence from the manifest, *not* by the Mongo update. `repository.update_document` silently no-ops on an unknown `_id` (`repository.py:78-109`), so it cannot be used to detect a missing document.

### D6 — Mongo is not touched at all (revised during implementation)

The plan called for bumping `updated_at` via `repository.update_document` for a truthful "last changed" on the documents list. **Dropped after checking the consumer:** the frontend does not surface a guidance document's `updatedAt` anywhere — every `updatedAt` in that codebase belongs to publishing/review *job* runs, not documents.

So the bump had no observable effect, while costing `section_writer` a second repository dependency, a get-then-update round trip, and a silent-no-op path (`update_document` does not check `matched_count`) that could be mistaken for a 404 signal. Dropping it leaves the writer with one dependency and one job: the S3 artefacts.

Reinstate it if and when a "last edited" column appears in the UI.

### D6a — A heading is stored verbatim; no duplicate-number stripping

The plan called for defensively stripping a leading section number if an editor typed one into the heading field (so `"1 Overview"` in section 1 would not become `## 1 1 Overview`).

**Dropped.** The heuristic cannot distinguish a duplicated number from a legitimate heading that happens to begin with one: section 7 headed **"7 day rule"** would be silently mangled to "day rule". Corrupting real content is worse than rendering a visible duplicate the editor can see and fix themselves.

The frontend never puts the number in the field, so a duplicate only arises from deliberate typing. A test asserts that `"7 day rule"` in section 7 survives verbatim.

### D6b — Line endings are normalised on write

An HTML `<textarea>` posts CRLF per the HTML spec. Stored Markdown is normalised to LF-only, and surrounding blank lines are trimmed, so a file does not accumulate blank lines or mixed endings across saves and stays byte-comparable with parser output.

### D6c — Only ASCII whitespace is trimmed, never U+00A0

Found by end-to-end testing, not by unit tests: after correcting one word in a real imported document, a diff of `content.md` showed a **second**, unintended change — a non-breaking space had vanished from the end of a section.

Cause: `str.strip()` treats U+00A0 as whitespace. But a non-breaking space is a character the author chose, and Word-derived guidance is full of them, so trimming it silently edits content the user did not touch — the same failure mode rejected in D6a.

Body trimming is therefore restricted to `" \t\r\n"`. With that fix, saving a section unchanged reproduces the stored bytes exactly, and the only difference between the originally-parsed `content.md` and the post-edit one is the intended word. The frontend's split helper had the identical bug via `String.trim()` and was fixed the same way.

**Lesson worth keeping:** byte-diffing a real document before and after an edit caught a class of bug that passing unit tests did not.

### D7 — Last-writer-wins, no versioning or audit

Consistent with the feature's agreed scope; neither app has authentication, so there is no identity to attribute an edit to. A repeat uploader callback re-parses the document and overwrites edits. Accepted.

### D8 — Completed job results are not retro-corrected

`start_analysis` / `start_review` snapshot the text into the submitted task (`publishing/jobs/service.py:75`, `review/jobs/service.py:74`), and the executor never re-reads S3. So only jobs started *after* an edit see it, and existing findings keep quoting pre-edit text. Accepted: results are a record of what was checked at the time.
