# Troubleshooting and format notes

## Contents
- Known rendering problems and their fixes
- Setup problems
- The .laf / .lcd format
- How the conversion works (if you need to change the converter)

## Known rendering problems

| Symptom | Cause | Status |
|---|---|---|
| Shapes are solid black and their text is hidden | Newer Lucid documents leave shape styles `"unset"` and refer to a theme preset (`StylePresetId` → `state.Elements[id]`, `Type: "ShapeStylePreset"`). LucidImporter doesn't resolve presets. | Fixed in `convert.mjs` (`resolveStylePresets`). If it comes back, check for other `"unset"` properties, e.g. on `Lines` with a `LineStylePreset`, and extend the same mapping. |
| Document fails with `Cannot read properties of undefined (reading 'drawShape')`, or AWS/Azure/Kubernetes/flowchart icons are blank | `export3.html` loads stencil libraries from `stencils/*.xml`, which the repository doesn't contain; they're bundled in `js/stencils.min.js` (plus coded shapes in `js/shapes-*.min.js`) | Fixed: `convert.mjs` injects both bundles into the export page. Any 404 while rendering now appears as a warning in the report. |
| A PNG shows the diagram tiled/repeated | Chrome tiles screenshots whose clip is bigger than the viewport the page was rendered in | Fixed: each page is measured, then re-rendered in a viewport sized to the content. |
| Curved lines take odd paths or start in empty space | Lucid curves through every waypoint; draw.io smooths differently | Known, accepted. Fix by hand in the `.drawio` file if it matters. |
| PDF fails with `Printing failed`; the page is ~1.5 million px across | A Lucid line starts or ends **on another line** (`Endpoint.Line` + `LineP`). Lucid stores a placeholder position near (-1000000, -1000000) and LucidImporter uses it, so the connector shoots off the page | Fixed: `resolveLineToLineEndpoints` puts the endpoint at `LineP` along the target line (straight-line approximation). |
| `Execution context was destroyed, most likely because of a navigation` | Calling `render()` synchronously inside `page.evaluate` loses the evaluation for some documents (no real navigation happens) | Fixed: `render()` is started from a timer and completion is detected by polling for `#LoadingComplete`. |
| PNG screenshot times out, or a page is huge | Chrome can't capture images over ~16384 px per side, and very large captures stall | Fixed: such pages are scaled down to fit (16000 px per side, 100 Mpx total) and the report notes it. The PDF keeps full size. |
| Logos/pictures missing, or `HTTP 404 …/imageBlocks/image/<id>` | Uploaded images are stored as relative links that only resolve on Lucid's site | Fixed: requests for `/imageBlocks/…` are fetched from `https://lucid.app` (they're publicly readable by ID). Offline, or after the account is closed, they may still be missing. |
| Text in a different font | The Lucid font isn't installed locally; Chrome substitutes | Install the font, then rerun that document with `--force`. |
| A document fails with a timeout | Very large page (thousands of shapes) or out of memory | Rerun alone: `--only "<name>" --force --concurrency 1`. |
| `LucidImporter produced no diagram` | The document is empty, or uses a Lucid product the importer doesn't know | Check `product` in `index.csv`. Only `chart` and `spark` have been seen. |

When you find a new rendering problem: look at that document's JSON (`state` → `Pages` → `Blocks` /
`Lines`, each with `Class` and `Properties`), compare it with the generated `.drawio` XML (the `style`
attributes), and fix the input in `convert.mjs` before import, the way `resolveStylePresets` does. That's
better than patching draw.io's minified code.

## Setup problems

- **"Executable doesn't exist at …ms-playwright…"**: no browser for Playwright. Install Google Chrome, or
  run `npx playwright install chromium` inside the tools folder.
- **`git fetch` of the pinned commit fails**: the network blocks GitHub, or the commit is gone. Try
  `DRAWIO_REF=dev bash setup.sh` for the latest draw.io. That's untested, so do a test run after.
- **Windows**: the scripts are bash/Node/Python. Use WSL, or run the three commands by hand
  (`setup.sh` is short and readable).

## The .laf / .lcd format

```
Lucidchart Archive File\n
FOLDERS_METADATA\n
{"version":"2","folderStructure":[{"id","name","parentId"}...],"documentFolders":{"<doc uuid>":"<folder id>"}}\n
FILE:<doc uuid>\n
<zip archive containing one file: <doc uuid>.lcd>
FILE:<doc uuid>\n
...
```

`.lcd` is JSON: `{"Document": {"id", "title", "product": "chart"|"spark", "created", "state", ...}}`.
`state` is a JSON *string* containing `Properties`, `Pages` (each with `Properties.Title`, `Blocks`,
`Lines`, `Groups`), `Elements` (style presets, fonts, data), and top-level `Blocks`/`Lines` (usually empty
in newer docs). No thumbnails or rendered images are included. That's why rendering is needed.

## How the conversion works

1. Playwright opens `export3.html` from the draw.io checkout, served from disk under a fake origin
   (`http://drawio.local`). That page loads `app.min.js` (draw.io core) and `extensions.min.js` (which
   contains `LucidImporter`). The converter also injects `shapes-*.min.js` and `stencils.min.js`, which
   hold the shape libraries.
2. `LucidImporter.importState(document)` takes the `.lcd` `Document` object and returns draw.io XML
   (`<mxfile><diagram name="…">…`), one `<diagram>` per Lucid page. That XML is the `.drawio` file.
3. `render({xml, format:'pdf', allPages:'1', crop:'1'})` lays out every page for print, then
   `page.pdf({preferCSSPageSize:true})` saves it.
4. For PNG: `render({xml, format:'png', from:i, to:i})` sets `#LoadingComplete[bounds]`. The page is
   re-rendered in a viewport of that size and screenshotted at `deviceScaleFactor` = `--scale`.
