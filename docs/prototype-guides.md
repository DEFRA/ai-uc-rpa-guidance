# Prototype guides API

This document explains a small, self-contained feature added to this
repository: an API that lets a **GOV.UK Prototype Kit** read guidance
documents that have been parsed elsewhere, and lets an admin replace or
purge them. If you're building against this API for the first time, this doc has
everything you need — no prior context assumed.

## Why this exists

This backend already has a "real" guidance pipeline
(`app/guidance/documents/`): a user uploads a `.docx` through a web form, it
goes through the CDP-uploader service, gets parsed, and the result is stored
in Mongo + S3.

That pipeline is too heavyweight for prototyping. The **prototype guides**
feature is a parallel, much simpler path:

1. Someone runs a script locally
   (`scripts/parse_docx.py`, in the separate
   `rpa-ai-guidance-hub-api` repo, or `uv run task convert` in the
   `rpa-ai-guidance-hub-dev` workspace) against a `.docx` file. It parses the
   document into Markdown + extracted images on disk.
2. That output directory is zipped and uploaded through the PoC frontend's
   Prototype guidance admin page (see
   [Loading new content](#loading-new-content) below). This API builds the
   manifest from what the zip holds.
3. This API (`/prototype/guides/...`) reads whatever is currently in that
   S3 location and serves it back over HTTP, so a prototype can fetch a
   guide's content and images without touching Mongo, the uploader, or any
   of the "real" pipeline.

The content itself is produced outside this codebase. The only writes here
are unpacking an uploaded zip of it and purging it, both confined to
`prototype_guides/`. Nothing about the existing `/guidance/documents/...`
endpoints changes; this is additive.

## Where the data lives in S3

Everything sits under one prefix in the same S3 bucket the real pipeline
uses (`GUIDANCE_S3_BUCKET`), just under a different top-level folder so the
two features' data can never collide:

```
s3://<bucket>/prototype_guides/manifest.json
s3://<bucket>/prototype_guides/<document_id>/<version_id>/content.md
s3://<bucket>/prototype_guides/<document_id>/assets/<asset_id>
```

- `<document_id>` and `<version_id>` are `uuid4` strings.
- A guide can have multiple versions (each re-parse of the `.docx` creates a
  new version), each with its own `content.md`.
- `assets/` sits **above** the versions, not inside one — every version of a
  guide shares the same asset store. Assets are named by a content digest
  (a hash of the image bytes), so re-parsing a guide that keeps an unchanged
  picture never creates a duplicate file.

### `manifest.json`

This is the index: it maps a guide's human-readable name to its document id
and the ordered list of its versions. **This API builds it when a zip is
unpacked**; the parser no longer writes one, and any `manifest.json` in a zip
is ignored. Example:

```json
{
  "cs-ma-claim-revenue-option-claim-rule-at-signoff": {
    "documentId": "37bef251-3242-4b70-948a-a1d05a3839d1",
    "title": "CS MA Claim – Revenue Option Claim Rule at Signoff",
    "createdAt": "2026-09-28T17:05:24.718264+00:00",
    "updatedAt": "2026-09-28T17:05:24.718264+00:00",
    "latestVersion": 1,
    "versions": [
      {
        "version": 1,
        "versionId": "1529fe97-33c0-43d1-9413-a9edb331cb19",
        "createdAt": "2026-09-28T17:05:24.718264+00:00",
        "updatedAt": "2026-09-28T17:05:24.718264+00:00",
        "sections": 26,
        "images": 42,
        "contentUrl": "37bef251-3242-4b70-948a-a1d05a3839d1/1529fe97-33c0-43d1-9413-a9edb331cb19/content.md"
      }
    ]
  }
}
```

- The outer key (`cs-ma-claim-revenue-option-claim-rule-at-signoff`) is a
  slug of the title — lowercase letters and digits, hyphenated, with `-2`,
  `-3`… added if two titles clash, or the `documentId` if there is no title.
  You generally don't need it, because the API is addressed by `documentId`.
- `title` is the latest version's first line (`# <title>`). `sections` counts
  its headings below the title, and `images` its image references.
- A guide's versions are ordered by when each `content.md` was written, as
  the zip records it, oldest first and numbered from 1; the newest is
  `latestVersion`. Versions written at the same moment (zip times are only
  to two seconds, and copying files can reset them) are ordered by
  `versionId` and logged, since which is latest is then a guess.
- `contentUrl` is the S3 key **relative to `prototype_guides/`**. Join it
  onto that prefix to get the real key, e.g.
  `prototype_guides/37bef251-.../1529fe97-.../content.md`. You never need to
  build this yourself though — the API does it for you.
- `createdAt` and `updatedAt` come from the same zip times, read as UTC (a
  zip records no time zone, so they may be an hour out in summer). A guide's
  `createdAt` is its first version's and its `updatedAt` its latest's; a
  version's two are the same, since a version never changes once parsed. Both
  are optional in the schema, as manifests from older versions of the parser
  lacked them.
- To find "the latest version of document X", the API scans every guide
  entry for the one whose `documentId` matches X, then looks up its
  `latestVersion` number in that entry's `versions` list.

### Images inside the Markdown

Inside `content.md`, images are referenced as relative links pointing at the
shared `assets/` folder, e.g.:

```markdown
![](../assets/7325bba64eff8125faf047e2fd3d2719acc4e0b68ff0362cbf63f212fa27b00e.png)
```

A prototype consuming this Markdown needs to rewrite these relative paths to
hit the `/assets/{asset_id}` endpoint below (see
[Rendering images](#rendering-images-in-the-markdown)) — the API does not
serve raw filesystem-style relative paths.

## API endpoints

Base path: `/prototype/guides` (assuming the service is running at
`localhost:8085` locally).

### 1. `GET /prototype/guides/manifest`

Returns the whole manifest as JSON, keyed by guide name.

```bash
curl http://localhost:8085/prototype/guides/manifest
```

- `200` with the manifest body on success.
- `404 {"detail": "Manifest not found"}` if `manifest.json` hasn't been
  synced yet.

Use this to discover what guides exist and their `documentId`s — you
generally call this once, up front, rather than before every content
request.

### 2. `GET /prototype/guides/{document_id}/content`

Returns the guide's Markdown content as `text/markdown`.

```bash
# Latest version (no version_id) — the API resolves the manifest for you
curl http://localhost:8085/prototype/guides/37bef251-3242-4b70-948a-a1d05a3839d1/content

# A specific, known version
curl "http://localhost:8085/prototype/guides/37bef251-3242-4b70-948a-a1d05a3839d1/content?version_id=1529fe97-33c0-43d1-9413-a9edb331cb19"
```

- `version_id` query param is **optional**.
  - If given, the API reads that exact version's `content.md` directly — it
    doesn't even need to look at the manifest.
  - If omitted, the API fetches the manifest, finds the guide matching
    `document_id`, and resolves its `latestVersion`.
- `404 {"detail": "No guide <document_id>"}` if that document id isn't in
  the manifest at all (only checked when `version_id` is omitted).
- `404 {"detail": "Content not found"}` if the resolved S3 object itself is
  missing.

### 3. `GET /prototype/guides/{document_id}/assets/{asset_id}`

Returns the raw bytes of an image (or other asset) referenced from the
guide's Markdown.

```bash
curl "http://localhost:8085/prototype/guides/37bef251-3242-4b70-948a-a1d05a3839d1/assets/7325bba64eff8125faf047e2fd3d2719acc4e0b68ff0362cbf63f212fa27b00e.png" \
  -o image.png
```

- `version_id` query param is **optional**, same resolution rules as
  `/content` above. It's used only to confirm the document/version pair
  exists — **it does not select a different asset**. Because assets are
  shared across every version of a guide, passing a different, equally
  valid `version_id` for the same `document_id` returns identical bytes.
- Content type is inferred from the file extension (`.png` → `image/png`,
  etc.), falling back to `application/octet-stream`.
- `404 {"detail": "No guide <document_id>"}` if the document/version pair
  doesn't resolve.
- `404 {"detail": "Asset not found"}` if the asset object itself is missing.

## Rendering images in the Markdown

Because `content.md` uses relative paths like `../assets/<digest>.png`, a
prototype needs to translate them before display. In practice this means:
find every `../assets/<filename>` reference in the fetched Markdown and
replace it with
`/prototype/guides/{document_id}/assets/{filename}` (proxied through
whatever base URL your prototype uses for this API), then render the
Markdown as normal.

## Loading new content

### Uploading a zip

Zip the directory `scripts/parse_docx.py` wrote to, and upload it from
the PoC frontend's Prototype guidance admin page (`/admin/prototype-guides`).
The guides (`<document_id>/<version_id>/content.md`, with `assets/`) must be at
the top level of the zip, or inside a single top-level directory (which is
what zipping the directory itself gives you). **A zip with no files at all is
accepted**: it purges every guide, writes no manifest, and is logged.

**An upload replaces every guide.** The zip goes through CDP uploader like
any other upload:

1. `POST /prototype/guides/uploads` with `{"redirect": "<url>"}` opens a CDP
   uploader session and returns `{"uploadId": "..."}`; the browser posts the
   zip to CDP uploader against that id. CDP uploader stores it under
   `prototype_uploads/` in the same bucket.
2. Once it is scanned, CDP uploader calls
   `POST /prototype/guides/uploads/callback`. The zip is read where it lies,
   by byte range from S3, and one entry at a time, so neither it nor its
   contents are ever held in memory. It is checked in full first: its index
   (guides where expected, no entry that would escape
   `prototype_guides/`, at most 5,000 files and 400 MB unzipped), then every
   entry read through to check its CRC, and the manifest built from each
   version's `content.md`. Only then is `prototype_guides/` purged, each entry
   streamed into it, and the manifest written last.

Uploads are limited to 350 MB; CDP uploader rejects anything larger before
it reaches the bucket. The limits are about ten times the guides as first
uploaded (a 35 MB zip of 443 files). Any problem reading the zip, from the
zip or from S3, is treated as the zip being corrupt.

A zip that fails the check -- not a zip, corrupt, files but no guides where
expected, or an entry that would escape `prototype_guides/` -- leaves the
current guides untouched. Its callback is still answered `204`, because CDP
uploader retries a failed callback and retrying cannot fix the zip; the
rejection is logged.

The zip itself is deleted from `prototype_uploads/` once it has been dealt
with: after a successful unpack, or when it fails the check. Any other failure
(S3 erroring part-way, say) keeps it, so the uploader's retry can try again.
Because the callback is unauthenticated and names its own bucket and key, it
only ever reads or deletes a file under `prototype_uploads/` in the guidance
bucket; anything else is ignored.

### Purging

`DELETE /prototype/guides` deletes every object under `prototype_guides/`,
the manifest included, and returns `{"deleted": <count>}`. It cannot be
undone. Nothing outside the prefix is touched. If S3 reports any object as
not deleted, every batch is still attempted and then the purge fails with a
`500` naming how many were left; an upload whose purge fails writes nothing
and keeps its zip, so CDP uploader's retry can try again.

### Syncing by hand

Copying the parser's output up directly (`aws s3 sync`) no longer gives a
working set of guides on its own: the parser writes no manifest, and only an
upload builds one, so `GET /prototype/guides/manifest` would answer `404`.
Upload a zip instead.

## What this feature deliberately does *not* do

- No Mongo dependency of any kind — the whole thing only ever talks to S3.
- No relation to the CDP-uploader upload/callback flow used by
  `/guidance/documents`: uploads of guides have their own session, path and
  callback.
- No manifest writer — `manifest.json` is produced by the external parsing
  script and only ever unpacked from a zip here, never edited.
- Entirely isolated under `app/guidance/prototype/`: it can be deleted
  without touching `app/guidance/documents/` at all.
