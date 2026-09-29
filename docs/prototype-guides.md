# Prototype guides API

This document explains a small, self-contained feature added to this
repository: a read-only API that lets a **GOV.UK Prototype Kit** read
guidance documents that have been parsed elsewhere and copied into S3 by
hand. If you're building against this API for the first time, this doc has
everything you need — no prior context assumed.

## Why this exists

This backend already has a "real" guidance pipeline
(`app/guidance/documents/`): a user uploads a `.docx` through a web form, it
goes through the CDP-uploader service, gets parsed, and the result is stored
in Mongo + S3.

That pipeline is too heavyweight for prototyping. The **prototype guides**
feature is a parallel, much simpler path:

1. Someone runs a script locally
   (`scripts/parse_docx_for_s3.py`, in the separate
   `rpa-ai-guidance-hub-api` repo) against a `.docx` file. It parses the
   document into Markdown + extracted images on disk.
2. That output directory is copied up to S3 **by hand**, using
   `aws s3 sync` (see [Syncing new content](#syncing-new-content) below).
3. This API (`/prototype/guides/...`) reads whatever is currently in that
   S3 location and serves it back over HTTP, so a prototype can fetch a
   guide's content and images without touching Mongo, the uploader, or any
   of the "real" pipeline.

There is **no writer** in this repository for this data — it is entirely
produced outside this codebase and only ever read here. Nothing about the
existing `/guidance/documents/...` endpoints changes; this is additive.

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
and the ordered list of its versions. Example (real data from a synced
guide):

```json
{
  "cs-ma-claim---revenue-option-claim-rule-at-signoff-2026": {
    "documentId": "37bef251-3242-4b70-948a-a1d05a3839d1",
    "title": "CS MA Claim – Revenue Option Claim Rule at Signoff",
    "latestVersion": 1,
    "versions": [
      {
        "version": 1,
        "versionId": "1529fe97-33c0-43d1-9413-a9edb331cb19",
        "createdAt": "2026-09-28T17:05:24.718264+00:00",
        "sections": 26,
        "images": 42,
        "contentUrl": "37bef251-3242-4b70-948a-a1d05a3839d1/1529fe97-33c0-43d1-9413-a9edb331cb19/content.md"
      }
    ]
  }
}
```

- The outer key (`cs-ma-claim---revenue-option-claim-rule-at-signoff-2026`)
  is just a human-friendly slug — you generally don't need it, because the
  API is addressed by `documentId`.
- `contentUrl` is the S3 key **relative to `prototype_guides/`**. Join it
  onto that prefix to get the real key, e.g.
  `prototype_guides/37bef251-.../1529fe97-.../content.md`. You never need to
  build this yourself though — the API does it for you.
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

## Syncing new content

This API never writes anything — content only appears after someone runs
`aws s3 sync` by hand. See the repo's other sync documentation for the exact
commands, but in short:

```bash
aws s3 sync ./output/ s3://<bucket>/prototype_guides/ [--endpoint-url http://localhost:4566]
```

where `./output/` is whatever directory `scripts/parse_docx_for_s3.py`
produced. `--endpoint-url` is only needed when syncing to the local floci S3
emulator rather than real AWS.

## What this feature deliberately does *not* do

- No Mongo dependency of any kind — the whole thing only ever talks to S3.
- No relation to the CDP-uploader upload/callback flow used by
  `/guidance/documents`.
- No manifest writer — `manifest.json` is produced by the external parsing
  script, not by this service.
- Entirely isolated under `app/guidance/prototype/`: it can be deleted
  without touching `app/guidance/documents/` at all.
