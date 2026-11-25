# Photos and videos from your own phone (originals)

Photos you took but never shared on WhatsApp are not in any chat export. Copy them from the phone and catalog them; they then appear in Review alongside the WhatsApp media.

1. Connect the phone by USB and choose **File transfer**. In Windows Explorer open the phone → Internal storage → `DCIM\Camera` (also `Pictures` / `Movies` if you use other camera apps). Sort by date and copy the wedding-period files to `D:\Wedding-2026\Phone-Originals\<Label>\`, e.g. `Padhu-Phone`. One folder per phone or camera.
2. Catalog them (files stay where they are):

```bash
pip install -e '.[photos]'      # once: Pillow, for camera dates inside photos
python -m media_archive.folder_import /mnt/d/Wedding-2026/Phone-Originals/Padhu-Phone \
  --label Padhu-Phone --owner 'Padhu Ramalingam' --since 2026-11-01 --dry-run
```

Drop `--dry-run` to record them, then `python -m media_archive.review build` to add them to Review as `<date>_<time>_<Owner>_<Label>.jpg`.

- Time taken comes from the photo's EXIF data, else the file name (`IMG_20261115_103012`, `PXL_…` (UTC), `VID_…`, `Screenshot_…`), else the video's internal creation time, else the file date (reported as a note).
- **Time zones.** WhatsApp exports show every message in the zone the phone was in *when exporting*. Camera times are the local clock where each photo was taken. To line both up, all times are converted to one reference zone — set it to the export zone with `"timezone": "Asia/Kolkata"` in the config (or `--timezone`). Where the local clock was elsewhere, add rules: `--zone-until 2026-11-02=America/New_York` (local times before 2 Nov were US Eastern; daylight-saving changes are handled), or in the config `"zone_until": ["2026-11-02=America/New_York"]`. Exact offsets recorded in a photo (EXIF OffsetTimeOriginal) or video (UTC creation time) take precedence over the rules; a video's internal time is ignored if it disagrees with its file name by more than 30 minutes. The manifest keeps `taken_local`, `taken_utc_offset` and `zone_basis`; Review names use the reference-zone time.
- Identical files are cataloged once; re-running only adds new files. If a copy was interrupted, re-copy over it (choose *Replace*) and re-run: records whose file content changed are replaced, and `review build` removes the outdated Review copies. The manifest is `Metadata/folder-import/manifest.jsonl`.
- The same photo shared on WhatsApp is a recompressed copy, so it is not detected as identical; both appear in Review. Keep the original.
