---
name: sharepoint-video-download
description: Download a Microsoft SharePoint / OneDrive / Microsoft Stream / Teams meeting-recording video to a local .mp4 using the user's own logged-in browser session — including videos where the "Download" button is missing or download is blocked, as long as the user can play the video. Use whenever the user wants to save, download, grab, or keep a video hosted on sharepoint.com, *-my.sharepoint.com, svc.ms, or microsoftstream.com, or a Teams/Stream meeting recording, especially when they mention there is no download option or it is view-only.
---

# sharepoint-video-download

## Purpose

Save a local `.mp4` of a SharePoint / OneDrive / Microsoft Stream / Teams
recording that the user is authorized to watch, even when the player exposes no
Download button. It reuses the user's own signed-in browser session, so nothing
is cracked or bypassed.

## How it works (why this is safe and reliable)

SharePoint/Stream players expose the recording's real address on the page as
`window.g_fileInfo['.transformUrl']`. That URL already embeds a short-lived
`tempauth` token tied to **the current signed-in user's own session** — the same
authorization the browser uses to stream the video. Rewriting it into the DASH
`videomanifest` endpoint yields a manifest that `yt-dlp` can fetch and merge into
an mp4. The "block download" flag is a policy setting on the file, not a security
boundary, and the token used is the user's own.

This is the same mechanism as the open-source SharePointVideoDownloader desktop
app, implemented natively so it works cross-platform without installing that
Windows app or a browser extension.

## Scope

- In scope: on-demand SharePoint/OneDrive videos and Stream/Teams meeting
  recordings the user can play in their browser.
- Only proceed when the user has legitimate access (they can play it) and wants a
  copy. If a real Download button exists, use it — this skill is for when it does
  not.
- Out of scope: live (still-recording) events (no static manifest yet — wait for
  publication); genuinely DRM/IRM-encrypted content (the manifest fetch will
  fail — direct the user to the file owner or a tenant admin instead).

## Prerequisites

`yt-dlp` and `ffmpeg` on PATH:
- macOS: `brew install yt-dlp ffmpeg`
- Debian/Ubuntu: `sudo apt install ffmpeg && pipx install yt-dlp` (or `pip install -U yt-dlp`)

## Steps

1. **Get the video playing in a browser you can script.** Use whatever
   browser-automation / devtools access the host agent provides. The active page
   origin should be `*.sharepoint.com`, `*.svc.ms`, or `*.microsoftstream.com`.
   If the user gives a URL, open it and let the player load. The user must be
   signed in and able to play the video; never enter their credentials for them.

2. **Extract the manifest URL** by running this JavaScript **in the page context**
   (browser-automation JS-eval, or the devtools console). It reads `.transformUrl`,
   rewrites the endpoint to `videomanifest`, and adds the DASH parameters:

   ```js
   (() => {
     const g = window.g_fileInfo;
     if (!g) return { ok:false, reason:"g_fileInfo undefined — make sure a video player page is fully loaded" };
     const t = g['.transformUrl'] || g['.providerCdnTransformUrl'];
     if (!t) return { ok:false, reason:"no transformUrl on this page" };
     const u = new URL(t);
     u.pathname = u.pathname.replace(/\/transform\/.*$/, '/transform/videomanifest');
     u.searchParams.set('part', 'index');
     u.searchParams.set('format', 'dash');
     return { ok:true, url: u.toString(), fileName: g.FileName || g.name || null };
   })();
   ```

   - `g_fileInfo undefined`: the player is still loading, the page is a
     folder/list rather than the video, or the video sits in an iframe — open the
     playing video (or target the iframe's frame) and retry.
   - Use the returned `fileName` (without extension) as the output name; fall back
     to the tab title or ask the user.

3. **Download promptly** — the `tempauth` token expires within minutes. Pipe the
   URL into the bundled script via **stdin** so the token never lands in argv,
   shell history, or logs. Run it from this skill's directory:

   ```bash
   printf '%s' '<the url from step 2>' | ./scripts/download.sh "<output basename>" [output-dir]
   ```

   Default output dir is `~/Downloads`. The script truncates any params after
   `format=dash`, runs `yt-dlp` (4 concurrent fragments) → `<name>.mp4`, merges
   with `ffmpeg`, and verifies the file has a real video duration before printing
   `OK`.

4. **Report** the final path, size, and duration. If the script exits non-zero
   with an expiry/duration error, the token lapsed — re-run step 2 for a fresh URL
   and try again.

## Handling the token safely

The extracted URL is a live credential to the user's account for its short life.
Do not paste the full token-bearing URL into chat, commit it, or write it into a
repo — the script's stdin + tempfile design exists precisely to avoid that. Any
temporary copy belongs in a scratch dir and should be deleted afterward.
