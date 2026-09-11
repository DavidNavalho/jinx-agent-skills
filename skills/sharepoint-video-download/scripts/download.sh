#!/usr/bin/env bash
# Download a SharePoint/Stream/OneDrive video from a DASH videomanifest URL.
#
# The manifest URL carries a short-lived `tempauth` token, so it is read from
# STDIN (never passed as an argument) to keep it out of the process list, shell
# history, and any command logs.
#
# Usage:
#   printf '%s' "<videomanifest-url>" | download.sh "<output-basename-without-ext>" [output-dir]
#
# Defaults: output-dir = ~/Downloads. Requires yt-dlp and ffmpeg on PATH.
set -euo pipefail

NAME="${1:?output basename required, e.g. \"AI Strategy Briefing\"}"
OUTDIR="${2:-$HOME/Downloads}"

command -v yt-dlp >/dev/null || { echo "ERROR: yt-dlp not found. Install with: brew install yt-dlp" >&2; exit 3; }
command -v ffmpeg >/dev/null || { echo "ERROR: ffmpeg not found. Install with: brew install ffmpeg" >&2; exit 3; }

URL="$(cat)"
if [ -z "${URL// }" ]; then echo "ERROR: no manifest URL on stdin" >&2; exit 2; fi
case "$URL" in
  *format=dash*) : ;;
  *) echo "ERROR: URL does not look like a DASH manifest (no format=dash). Rebuild it from g_fileInfo." >&2; exit 2 ;;
esac

# Anything after 'format=dash' (e.g. a trailing &version=Published) breaks the
# manifest fetch for some tenants; truncate to the anchor, matching the upstream tool.
URL="${URL%%format=dash*}format=dash"

mkdir -p "$OUTDIR"
OUT="$OUTDIR/$NAME.mp4"

# Batch-file keeps the token-bearing URL out of argv.
BATCH="$(mktemp -t spdl.XXXXXX)"
trap 'rm -f "$BATCH"' EXIT
printf '%s\n' "$URL" > "$BATCH"

UA="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"

yt-dlp \
  --newline \
  --no-playlist \
  --concurrent-fragments 4 \
  --retry-sleep 'fragment:exp=1:20' \
  --user-agent "$UA" \
  -o "$OUT" \
  --batch-file "$BATCH"

# Verify the result is a real, playable file rather than an auth/error page.
if [ ! -s "$OUT" ]; then echo "ERROR: output missing or empty: $OUT" >&2; exit 4; fi
DUR="$(ffprobe -v error -show_entries format=duration -of default=nk=1:nw=1 "$OUT" 2>/dev/null || echo 0)"
if ! awk "BEGIN{exit !($DUR > 0)}"; then
  echo "ERROR: downloaded file has no video duration — token may have expired or access is blocked." >&2
  exit 5
fi

SIZE="$(ls -lh "$OUT" | awk '{print $5}')"
printf 'OK  %s  (%s, %.0fs)\n' "$OUT" "$SIZE" "$DUR"
