# WhatsApp import from an Android chat export (recommended route)

WhatsApp's own **Export chat → Include media** on Android produces a ZIP with the chat text and the media files. Every attachment line carries the sender and the time, so provenance is exact and no browser automation is needed. (The export from WhatsApp Web contains text only — every photo is `<image omitted>` — but it is still useful as a checklist, see `--check`.)

## Before exporting (on the phone)

Scroll through the period you want and tap any photo or video that still shows a download arrow, including each photo inside albums. Media that never downloaded appears as `<Media omitted>` and has no file in the ZIP; the importer lists these by sender.

## Usage

Keep the original export ZIPs under `<archive_root>/Chat-Exports/`, unrenamed: the chat name is taken from the file name (`WhatsApp Chat with <name>.zip`). Import one or several at once:

```bash
python -m media_archive.whatsapp_import /mnt/d/Wedding-2026/Chat-Exports/*.zip --dry-run
```

Drop `--dry-run` to copy the files. Options:

- `--since` / `--until` (YYYY-MM-DD). Without `--since`, each chat uses its `groups` entry's `since` (matched by chat name), else the top-level `since` in the config, else its whole history.
- `--sender NAME`: exact sender as in the export, or its alias.
- `--types photo,video` (default). Also: `document` (PDF, Word, Excel, PowerPoint …), `audio`, `sticker`, `contact` (.vcf), `other`. Re-run with more types later to add them; already imported items are skipped.
- `--check WEB_EXPORT`: with a single ZIP, reconcile against the WhatsApp Web text export (`.zip` or `.txt`) by sender and minute. `--me "Your Name"` maps the web export's "You".
- `--date-order mdy|dmy`: normally detected from the dates in `IMG-YYYYMMDD-WA…` file names.

Quote single paths containing `!` with single quotes (bash treats `!` in double quotes as history expansion); globs such as `*.zip` need no quoting.

## Output

- Files: `<archive_root>/WhatsApp/Media/<sha256>.<ext>` — one shared store for all chats; a photo forwarded to several chats is stored once.
- Manifest: `<archive_root>/Metadata/whatsapp-import/manifest.jsonl`, one line per attachment per chat: `chat`, `sender`, `sender_raw`, `shared_at_local` (phone-local time, minute precision), `media_type`, `original_filename`, `caption`, `export_zip`, `export_line`, `local_path`, `sha256`, `size_bytes`, `imported_at`. A forwarded photo therefore has one file and several manifest lines (one per chat and sender).
- Re-running is safe: attachments already recorded (same chat, sender, time and file name, file still present) are skipped, so a later, larger export can be imported on top.

## Config

Only `archive_root` is required. Optional top-level `since` and `aliases` apply to every chat; a `groups` entry whose `name` equals the chat name overrides them for that chat. Aliases name unsaved contacts (the raw value is kept in `sender_raw`):

```json
{
  "archive_root": "/mnt/d/Wedding-2026",
  "since": "2026-10-01",
  "aliases": {"+1 (904) 900-9986": "Anu (cousin)"},
  "groups": [{"name": "Family Friends!!!", "directory": "Family-Friends", "since": "2026-11-11"}]
}
```

(`directory` is used only by the browser-based tools.)

## Limits

Times are minute precision and in the phone's time zone at export. The media are WhatsApp's copies, not camera originals. Chats longer than WhatsApp's export limit are truncated by WhatsApp. Do not commit exports, manifests or media.

Tests: `python -m unittest tests.test_whatsapp_import`.
