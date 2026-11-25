# WhatsApp media saved on the phone

Photos that are on the phone but not in the archive usually come from chats you have not exported, or from outside an export's date range. On Android they are stored as `IMG-YYYYMMDD-WAnnnn.jpg` (videos `VID-…mp4`) in

`Internal storage\Android\media\com.whatsapp\WhatsApp\Media\WhatsApp Images` (and `WhatsApp Video`), with a `Sent` subfolder for what you sent.

Chat exports made on the same phone name the same files, so each file can be traced to its chat, sender and time.

1. Copy the folders to the SSD, e.g. `D:\Wedding-2026\Phone-Originals\WhatsApp-Media\WhatsApp Images` (and `WhatsApp Video`). Sort by name and copy from the first wanted date (`IMG-20260801-…`) to limit the size.
2. Catalog (files stay in place):

```bash
python -m media_archive.whatsapp_folder /mnt/d/Wedding-2026/Phone-Originals/WhatsApp-Media --since 2026-08-01 --dry-run
```

The report shows files already in the archive (skipped), files traced to a message (cataloged with chat, sender, time), and files whose chat was not exported ("chat unknown", dated from the file name, with the time of day from the file's modified time when it matches). Unknown files are listed by day next to any not-downloaded media in your exports that day, to help decide which chats to export. Exporting those chats later and importing them adds the provenance; Review keeps one copy per file.

3. Drop `--dry-run`, then `python -m media_archive.review build`.

Manifest: `Metadata/whatsapp-folder/manifest.jsonl` (`match`: `export-file-name` or `none`; `sent_by_me` for files in `Sent`).
