#!/usr/bin/env python3
"""Extract every document from a Lucid backup archive (.laf) into a folder tree.

Usage: python3 -I extract_laf.py <backup.laf> <out-dir>

A .laf file is:
    Lucidchart Archive File\\n
    FOLDERS_METADATA\\n
    {"version": "2", "folderStructure": [...], "documentFolders": {...}}\\n
    FILE:<document-uuid>\\n<zip containing <uuid>.lcd>   (repeated, one per document)

Each .lcd is JSON: {"Document": {"title", "product", "created", "state", ...}}, where
"state" is itself a JSON string with Pages -> Blocks/Lines. Documents are written as
<folder path>/<title>.lcd, mirroring the folders in Lucid, plus an index.csv.
Only the standard library is used.
"""
import csv
import io
import json
import os
import re
import sys
import zipfile

MAGIC = b'Lucidchart Archive File\n'


def clean(name):
    """Make a Lucid title safe as a file or folder name (no path separators, no traversal)."""
    name = re.sub(r'[\x00-\x1f/\\:*?"<>|]', '_', (name or '').strip()).strip('. ')
    return (name or 'Untitled')[:120]


def assign_folder_paths(folders):
    """Map each Lucid folder id to a unique list of on-disk path components.

    Sibling folders can share a name, or have names that become identical once cleaned
    ("A/B" and "A:B" both become "A_B"), or that differ only in case. Without care they'd
    merge into one directory, so later siblings get " (2)", " (3)", ... in a stable order
    (by folder id). "(root)" is reserved at the top level for documents outside any folder.
    """
    children = {}
    for fid, f in folders.items():
        parent = f.get('parentId') if f.get('parentId') in folders else None
        children.setdefault(parent, []).append(fid)

    paths = {}
    used = {(): {'(root)'}}  # parent path -> lowercase names already taken there

    def place(fid, parent_path):
        taken = used.setdefault(parent_path, set())
        base = clean(folders[fid]['name'])
        name, n = base, 2
        while name.lower() in taken:
            name, n = f'{base} ({n})', n + 1
        taken.add(name.lower())
        paths[fid] = list(parent_path) + [name]
        stack.append((fid, tuple(paths[fid])))

    stack = []
    for fid in sorted(children.get(None, [])):
        place(fid, ())
    while True:
        while stack:
            fid, path = stack.pop()
            for child in sorted(children.get(fid, [])):
                if child not in paths:
                    place(child, path)
        # Folders caught in a parent cycle are never reached from the top; put them there.
        orphans = sorted(fid for fid in folders if fid not in paths)
        if not orphans:
            return paths
        place(orphans[0], ())


def main(src, out):
    data = open(src, 'rb').read()
    if not data.startswith(MAGIC):
        sys.exit(f'{src} does not look like a Lucid backup (.laf): missing "{MAGIC.decode().strip()}" header')
    if os.path.isdir(out) and os.listdir(out):
        sys.exit(f'{out} already exists and is not empty; choose a new output folder')

    meta_start = data.index(b'FOLDERS_METADATA\n') + len(b'FOLDERS_METADATA\n')
    meta = json.loads(data[meta_start:data.index(b'\n', meta_start)])
    folders = {f['id']: f for f in meta.get('folderStructure', [])}
    doc_folders = meta.get('documentFolders', {})

    folder_paths = assign_folder_paths(folders)

    def folder_path(fid):
        return folder_paths.get(fid, [])

    os.makedirs(out, exist_ok=True)
    entries = list(re.finditer(rb'FILE:([0-9a-f-]{36})\n', data))
    rows, used = [], set()
    for i, m in enumerate(entries):
        uid = m.group(1).decode()
        chunk = data[m.end(): entries[i + 1].start() if i + 1 < len(entries) else len(data)]
        try:
            z = zipfile.ZipFile(io.BytesIO(chunk))
            raw = z.read(z.namelist()[0])
            doc = json.loads(raw)['Document']
        except Exception as e:  # keep going; report at the end
            rows.append(dict(id=uid, error=f'{type(e).__name__}: {e}'))
            continue

        title = doc.get('title') or 'Untitled'
        pages = blocks = lines = 0
        try:
            state = json.loads(doc.get('state') or '{}')
            pages = len(state.get('Pages') or {})
            for pg in (state.get('Pages') or {}).values():
                blocks += len(pg.get('Blocks') or {})
                lines += len(pg.get('Lines') or {})
        except Exception:
            pass

        rel = os.path.join(*(folder_path(doc_folders.get(uid)) or ['(root)']))
        stem, name, n = clean(title), clean(title) + '.lcd', 2
        while os.path.join(rel, name).lower() in used:  # same title twice in one folder
            name, n = f'{stem} ({n}).lcd', n + 1
        used.add(os.path.join(rel, name).lower())
        os.makedirs(os.path.join(out, rel), exist_ok=True)
        with open(os.path.join(out, rel, name), 'wb') as f:
            f.write(raw)
        rows.append(dict(id=uid, title=title, folder=rel, file=os.path.join(rel, name),
                         product=doc.get('product'), created=doc.get('created'),
                         pages=pages, blocks=blocks, lines=lines, error=''))

    fields = ['title', 'folder', 'file', 'product', 'created', 'pages', 'blocks', 'lines', 'id', 'error']
    with open(os.path.join(out, 'index.csv'), 'w', newline='') as f:
        w = csv.DictWriter(f, fieldnames=fields, restval='')
        w.writeheader()
        for r in sorted(rows, key=lambda r: (r.get('folder', ''), r.get('title', ''))):
            w.writerow(r)

    failed = [r for r in rows if r.get('error')]
    ok = len(rows) - len(failed)
    products = {}
    for r in rows:
        if not r.get('error'):
            products[r['product']] = products.get(r['product'], 0) + 1
    print(f'Extracted {ok} of {len(entries)} documents into {out}  ({", ".join(f"{v} {k}" for k, v in products.items())})')
    print(f'Total pages: {sum(r.get("pages", 0) for r in rows if not r.get("error"))}. Index: {os.path.join(out, "index.csv")}')
    for r in failed:
        print(f'  FAILED {r["id"]}: {r["error"]}')
    sys.exit(1 if failed else 0)


if __name__ == '__main__':
    if len(sys.argv) != 3:
        sys.exit(__doc__)
    main(sys.argv[1], sys.argv[2])
