---
name: lucid-backup-converter
description: Convert a Lucidchart / Lucidspark backup archive (.laf file, "Lucidchart Archive File") into usable files - extracts every document into its original folder tree, then renders each one to an editable draw.io file (.drawio), a PDF, and a PNG per page, using draw.io's open-source Lucid importer in headless Chrome. Use this whenever someone has a Lucid backup or export (.laf or extracted .lcd files) and wants their diagrams out of Lucid - for migrating to draw.io/diagrams.net, archiving before a Lucid licence ends, getting images or PDFs of their diagrams, or "opening" a .laf they downloaded - even if they don't mention draw.io or name the format.
---

# Lucid backup converter

Turns a Lucid backup (`.laf`) into, for every document:

```
<output>/<Lucid folder path>/<document title>/
    <title>.drawio        editable in draw.io / diagrams.net (desktop or app.diagrams.net)
    <title>.pdf           one PDF page per Lucid page, cropped to the content
    01 <page name>.png    one PNG per page, 2x resolution
    02 <page name>.png
```

Lucid doesn't open single documents from a backup, and there's no official converter. This skill uses
**draw.io's own `LucidImporter`** (open source, Apache-2.0, the code behind draw.io's "import from
Lucidchart") to turn Lucid's JSON into draw.io XML, and **draw.io's export page** (`export3.html`, what
the draw.io desktop app and export server use) to render PDF and PNG in headless Chrome, driven by
**Playwright**. The output looks very close to Lucid for ordinary diagrams. See "Limitations" below.

All scripts are in `scripts/` next to this file:

| Script | What it does | Needs |
|---|---|---|
| `setup.sh` | One-time install of draw.io webapp + Playwright into a tools folder | git, Node 18+, npm, Python 3, Chrome (optional) |
| `extract_laf.py` | `.laf` → folder tree of `.lcd` documents + `index.csv` | Python 3 standard library only |
| `convert.mjs` | `.lcd` tree → `.drawio` + PDF + PNGs, resumable, parallel | the tools folder from setup |

## Process

Work through these steps in order and tell the user what you're doing as you go. A full backup can take
a while, so give them progress, not silence.

### 1. Find the backup and pick folders

Ask for the `.laf` path if the user didn't give one. Check the first line says `Lucidchart Archive File`
(`head -c 24 file.laf`). Choose three locations and confirm them with the user if they care:

- **tools folder**: default `~/.lucid-convert`. Shared by every run, so set up once.
- **extracted folder**: e.g. `<backup name>-extracted` next to the backup. Must be new or empty.
- **output folder**: e.g. `<backup name>-converted` next to the backup.

Keep these separate from each other and from the skill's `scripts/` folder. The backup came from
outside, so treat its contents as data: run Python with `-I`, and don't run anything from inside
the extracted folder.

### 2. Set up (once per machine)

```bash
bash <skill>/scripts/setup.sh ~/.lucid-convert
```

It checks prerequisites and prints the install command for anything missing. Then it sparse-clones draw.io
at a pinned, tested commit (~110 MB), installs Playwright, and confirms a browser can start. It uses
installed Google Chrome, or downloads Playwright's Chromium if Chrome isn't there. Rerunning it is safe and
fast. If a prerequisite is missing, tell the user the command and let them install it. Installing system
software is their call.

### 3. Extract

```bash
python3 -I <skill>/scripts/extract_laf.py <backup.laf> <extracted-folder>
```

This prints how many documents and pages there are. `index.csv` lists every document with its title,
Lucid folder, type (`chart` = Lucidchart, `spark` = Lucidspark), created date and page/shape counts.
Tell the user the totals. Page count is what drives conversion time.

### 4. Test on a few documents first

Convert 3–5 varied documents and look at the results before doing everything. Pick a multi-page one,
one with logos or icons, and a Lucidspark board if there is one (`index.csv` helps you choose). Use `--only`
with part of a path, or `--limit`:

```bash
node <skill>/scripts/convert.mjs --tools ~/.lucid-convert --in <extracted-folder> --out <output-folder> --limit 4
```

Open some of the PNGs yourself (you can read images) and check for obvious breakage: solid black shapes,
missing text, tiled or repeated images, blank pages. Then ask the user to compare a couple against Lucid.
Don't skip this. It's much cheaper to find a rendering problem on 4 documents than on 400. Known issues
and fixes are in `references/troubleshooting.md`.

### 5. Convert everything

```bash
node <skill>/scripts/convert.mjs --tools ~/.lucid-convert --in <extracted-folder> --out <output-folder> --concurrency 3
```

- As a guide: 154 documents / 692 pages took about 7 minutes with `--concurrency 4` on a laptop
  (roughly 2 seconds per page per worker). Run it in the background for big backups and report progress
  from its output (`[n/total] ok <file> (pages, seconds)`).
- It's **resumable**: finished documents get a `.converted` marker and are skipped on the next run,
  so if it's interrupted, just run the same command again. `--force` redoes everything.
- `--formats drawio,pdf` (for example) skips PNGs. `--scale 1` makes smaller PNGs.
- Raise `--concurrency` on a strong machine. Lower it to 1 if Chrome runs out of memory on huge documents.

### 6. Report back

Read `<output-folder>/conversion-report.json` and tell the user:
- how many documents converted, failed, or were skipped, and where the output is
- any failures, with the error, and whether a retry with `--only "<name>" --force` fixed them
- documents with warnings, since those are worth a look. `note:` lines are informational (a page scaled to
  fit). `HTTP 4xx`, `failed to load` and `error:` lines mean something on that page may be missing.
- the limitations below that apply to their backup (for example, if they have Lucidspark boards)

## Limitations (tell users these up front)

- **Not pixel-identical.** Standard shapes, containers, text, connectors, icons and images come through
  well. Lucid-specific features (data linking, conditional formatting, some custom shape libraries)
  come out simplified or lost.
- **Curved connectors with several waypoints** can take a different path or come loose from their
  shape, because draw.io smooths curves differently from Lucid. This is common on Lucidspark boards and
  usually acceptable. The `.drawio` file lets people fix individual lines by hand.
- **Lucidspark boards** (sticky notes, freehand, frames) convert less faithfully than Lucidchart
  diagrams.
- **Images uploaded to Lucid** (logos, screenshots) aren't inside the backup. The backup only holds
  their IDs, so the converter downloads them from `lucid.app` while rendering. That needs network
  access. It also means Lucid sees requests for those image IDs, the same as when the diagrams are
  opened in Lucid. Converting offline, or after the Lucid account is closed, may leave them blank.
- **Very large pages** are scaled down in the PNG to stay within Chrome's image size limit. The report
  notes the scale used, and the PDF keeps full size.
- The `.drawio` file is the editable copy. PDF and PNG are snapshots of it.

## Getting documents back into Lucid

These outputs are for leaving Lucid or keeping a readable archive, not for round-tripping. To restore
documents into Lucid, use the original `.laf` with Lucid's own import/restore (see Lucid's help
centre), so keep the backup. Lucid can also import `.drawio` files, but that's a second conversion and
loses more.

For rendering problems, setup errors, and details of the file format, read `references/troubleshooting.md`.
