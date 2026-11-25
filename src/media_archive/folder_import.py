"""Catalog photos and videos copied from a phone or camera (originals, not WhatsApp copies).

Files are left where they are (keep them under <archive_root>/Phone-Originals/<label>/); each
is hashed and recorded once in Metadata/folder-import/manifest.jsonl with the time it was taken:

  1. EXIF DateTimeOriginal (photos; needs Pillow: pip install -e '.[photos]')
  2. the file name: IMG_20261115_103012.jpg, VID_…, 20261115_103012.jpg, Screenshot_…,
     PXL_20261115_050012345.jpg (Pixel names are UTC and are converted)
  3. the MP4/MOV creation time (UTC, converted)
  4. the file's modified time (least reliable; reported)

All times are converted to one reference zone (--timezone; use the zone the WhatsApp chats
were exported in, so both sources line up). Exact offsets are used where the file records
them (EXIF OffsetTimeOriginal, MP4 UTC, Pixel names); otherwise --zone-until rules say where
the local clock was, e.g. --zone-until 2026-11-02=America/New_York. The local clock time and
UTC offset are kept in the manifest as taken_local / taken_utc_offset.
"""
import argparse
import hashlib
import json
import re
import struct
import sys
from collections import Counter
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

PHOTO_EXT = {'.jpg', '.jpeg', '.png', '.heic', '.heif', '.webp', '.dng', '.gif'}
VIDEO_EXT = {'.mp4', '.mov', '.3gp', '.mkv', '.m4v'}
NAME_TIME = [  # (regex, is_utc)
    (re.compile(r'^PXL_(\d{8})_(\d{6})'), True),
    (re.compile(r'(?:^|[_-])(20\d{6})[_-]?(\d{6})'), False),  # IMG_20261115_103012, 20261115_103012, Screenshot_20261115-103012
]


def sha256(path):
    h = hashlib.sha256()
    with path.open('rb') as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b''):
            h.update(chunk)
    return h.hexdigest()


class Unreadable:
    """Collects files that cannot be read; stops the run if the drive seems to have gone away."""
    LIMIT = 20

    def __init__(self):
        self.files, self.streak = [], 0

    def ok(self):
        self.streak = 0

    def add(self, path, exc):
        self.files.append((str(path), exc.strerror or str(exc)))
        self.streak += 1
        if self.streak >= self.LIMIT:
            raise SystemExit(f'{self.LIMIT} files in a row could not be read (last: {path}: {exc}). '
                             'The drive may have disconnected: check "ls /mnt/d", remount, and re-run. '
                             'Nothing was recorded for this run.')

    def report(self):
        if self.files:
            print(f'{len(self.files)} files could not be read and were skipped (re-copy them, or check the drive):')
            for f, err in self.files[:30]:
                print(f'  {f}  ({err})')


def parse_offset(text):
    m = re.fullmatch(r'\s*([+-])(\d{2}):?(\d{2})\s*', str(text or '').strip('\x00'))
    if not m:
        return None
    delta = timedelta(hours=int(m[2]), minutes=int(m[3]))
    return timezone(-delta if m[1] == '-' else delta)


def exif_time(path):
    """DateTimeOriginal as an aware time when the photo also records its UTC offset, else naive."""
    try:
        from PIL import Image
    except ImportError:
        return None
    try:
        with Image.open(path) as im:
            exif = im.getexif()
            sub = exif.get_ifd(0x8769)
            raw = sub.get(36867) or exif.get(306)  # DateTimeOriginal, else DateTime
            offset = parse_offset(sub.get(0x9011) or sub.get(0x9010))  # OffsetTimeOriginal / OffsetTime
        if not raw:
            return None
        stamp = datetime.strptime(str(raw).strip('\x00 '), '%Y:%m:%d %H:%M:%S')
        return stamp.replace(tzinfo=offset) if offset else stamp
    except Exception:
        return None


def name_time(name):
    """Camera file-name time: naive local clock time, or aware UTC for Pixel names."""
    for pattern, is_utc in NAME_TIME:
        m = pattern.search(name)
        if not m:
            continue
        try:
            stamp = datetime.strptime(m[1] + m[2], '%Y%m%d%H%M%S')
        except ValueError:
            continue
        return stamp.replace(tzinfo=timezone.utc) if is_utc else stamp
    return None


def mp4_time(path):
    """creation_time from the moov/mvhd box (seconds since 1904, UTC)."""
    try:
        with path.open('rb') as f:
            end = path.stat().st_size
            while f.tell() < end:
                header = f.read(8)
                if len(header) < 8:
                    return None
                size, kind = struct.unpack('>I4s', header)
                if size == 1:
                    size = struct.unpack('>Q', f.read(8))[0] - 8
                if kind == b'moov':
                    inner_end = f.tell() + size - 8
                    while f.tell() < inner_end:
                        s2, k2 = struct.unpack('>I4s', f.read(8))
                        if k2 == b'mvhd':
                            version = f.read(1)[0]; f.read(3)
                            secs = struct.unpack('>Q' if version else '>I', f.read(8 if version else 4))[0]
                            if not secs:
                                return None
                            return datetime(1904, 1, 1, tzinfo=timezone.utc) + timedelta(seconds=secs)
                        f.seek(s2 - 8, 1)
                    return None
                if size < 8:
                    return None
                f.seek(size - 8, 1)
    except (OSError, struct.error, IndexError):
        return None
    return None


def taken_at(path, kind, reference=None, rules=()):
    """(time, source). Aware when the zone is known exactly; naive = local clock where taken."""
    if kind == 'photo':
        t = exif_time(path)
        if t:
            return t, 'exif'
    named = name_time(path.name)
    if kind == 'video':
        t = mp4_time(path)  # should be UTC; some phones write local time, so cross-check the name
        if t and reference is not None and named is not None and named.tzinfo is None:
            local = t.astimezone(local_zone_of_instant(t, rules, reference)).replace(tzinfo=None)
            if abs((local - named).total_seconds()) > 30 * 60:
                return named, 'filename'  # video metadata disagrees with the camera's file name
        if t:
            return t, 'video-metadata'
    t = named
    if t:
        return t, 'filename'
    return datetime.fromtimestamp(path.stat().st_mtime, timezone.utc), 'file-modified'


def zone_for(day, rules, default):
    """rules: [(until_date, zone)] sorted; local clock times before until_date were in zone."""
    for until, zone in rules:
        if day < until:
            return zone
    return default


def local_zone_of_instant(aware, rules, reference):
    """Zone of the local clock at an exact instant, using the rules (which are in local dates)."""
    for until, zone in rules:
        if aware.astimezone(zone).date() < until:
            return zone
    return reference


def normalize(stamp, source, reference, rules):
    """-> (time in the reference zone, local clock time where taken, its UTC offset, basis)."""
    if stamp.tzinfo is None:                       # local clock time, zone not recorded
        zone = zone_for(stamp.date(), rules, reference)
        local = stamp.replace(tzinfo=zone)
        basis = 'reference-zone' if zone is reference else f'zone-rule:{getattr(zone, "key", zone)}'
    elif source == 'exif':                         # photo recorded its own UTC offset
        local, basis = stamp, 'recorded-offset'
    else:                                          # exact UTC (video metadata, Pixel name, file time)
        local, basis = stamp.astimezone(local_zone_of_instant(stamp, rules, reference)), 'utc'
    offset = local.strftime('%z')
    return (local.astimezone(reference).replace(tzinfo=None), local.replace(tzinfo=None),
            f'{offset[:3]}:{offset[3:]}' if offset else None, basis)


def catalog(src, root, label, owner, manifest, since=None, until=None, reference=None, rules=(), dry_run=False):
    reference = reference or datetime.now().astimezone().tzinfo
    existing = [json.loads(l) for l in manifest.read_text(encoding='utf-8').splitlines() if l.strip()] \
        if manifest.exists() else []
    by_path = {r['local_path']: r for r in existing}
    prior, seen, replaced = set(), set(), set()
    for row in existing:
        if (root / row['local_path']).is_file():
            prior.add(row['sha256'])
    unreadable = Unreadable()
    summary = {'added': Counter(), 'replaced': 0, 'duplicates': 0, 'already': 0, 'out_of_range': 0,
               'outside_root': 0, 'time_sources': Counter(), 'zone_basis': Counter(), 'skipped': Counter()}
    rows = []
    for path in sorted(p for p in Path(src).rglob('*') if p.is_file()):
        ext = path.suffix.lower()
        kind = 'photo' if ext in PHOTO_EXT else 'video' if ext in VIDEO_EXT else None
        if not kind or path.name.startswith('.'):
            summary['skipped'][ext or '(none)'] += 1
            continue
        try:
            rel = path.resolve().relative_to(root.resolve()).as_posix()
        except ValueError:
            summary['outside_root'] += 1
            continue
        raw, source = taken_at(path, kind, reference, rules)
        stamp, local, offset, basis = normalize(raw, source, reference, rules)
        if (since and stamp.date() < since) or (until and stamp.date() > until):
            summary['out_of_range'] += 1
            continue
        try:
            digest = sha256(path)
            unreadable.ok()
        except OSError as exc:
            unreadable.add(path, exc)
            continue
        old = by_path.get(rel)
        if old and old['sha256'] != digest:
            # Same file name, new content: e.g. a complete re-copy over an interrupted one.
            replaced.add(rel)
            summary['replaced'] += 1
            if not any(r['sha256'] == old['sha256'] for r in existing if r['local_path'] != rel):
                prior.discard(old['sha256'])
        if digest in prior or digest in seen:
            summary['already' if digest in prior else 'duplicates'] += 1
            continue
        seen.add(digest)
        summary['added'][kind] += 1
        summary['time_sources'][source] += 1
        summary['zone_basis'][basis.split(':')[0]] += 1
        rows.append({'chat': label, 'sender': owner, 'sender_raw': owner,
                     # shared_at_local is in the reference zone, like the WhatsApp export times.
                     'shared_at_local': stamp.isoformat(timespec='seconds'), 'timestamp_precision': 'second',
                     'reference_timezone': getattr(reference, 'key', str(reference)),
                     'taken_local': local.isoformat(timespec='seconds'), 'taken_utc_offset': offset,
                     'zone_basis': basis, 'time_source': source, 'media_type': kind, 'original_filename': path.name,
                     'caption': None, 'source': 'folder', 'local_path': rel, 'sha256': digest,
                     'size_bytes': path.stat().st_size, 'imported_at': datetime.now(timezone.utc).isoformat()})
    if not dry_run and replaced:
        keep = [r for r in existing if r['local_path'] not in replaced] + rows
        tmp = manifest.with_suffix('.tmp')
        tmp.write_text(''.join(json.dumps(r, ensure_ascii=False) + '\n' for r in keep), encoding='utf-8')
        tmp.replace(manifest)
    elif not dry_run and rows:
        manifest.parent.mkdir(parents=True, exist_ok=True)
        with manifest.open('a', encoding='utf-8') as f:
            for row in rows:
                f.write(json.dumps(row, ensure_ascii=False) + '\n')
    summary['rows'] = rows
    summary['unreadable'] = unreadable
    return summary


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument('folder', help='Folder of copied photos/videos, inside the archive root')
    p.add_argument('--label', required=True, help='Source name shown in Review, e.g. "Padhu-Phone"')
    p.add_argument('--owner', required=True, help='Whose photos these are, e.g. "Padhu Ramalingam"')
    p.add_argument('--config', default='config/whatsapp.local.json')
    p.add_argument('--since', type=date.fromisoformat)
    p.add_argument('--until', type=date.fromisoformat)
    p.add_argument('--timezone', help='Reference zone for all times, matching the zone the WhatsApp chats were '
                                      'exported in, e.g. Asia/Kolkata (default: config "timezone" or this machine)')
    p.add_argument('--zone-until', action='append', default=[], metavar='DATE=ZONE',
                   help='Local clock times before DATE were in ZONE, e.g. 2026-11-02=America/New_York '
                        '(repeatable; used when a photo does not record its own UTC offset)')
    p.add_argument('--dry-run', action='store_true')
    args = p.parse_args(argv)
    cfg = json.loads(Path(args.config).read_text(encoding='utf-8'))
    root = Path(cfg['archive_root']).expanduser()
    if not root.is_dir():
        p.error(f'Archive root not found: {root} (is the drive mounted?)')
    folder = Path(args.folder)
    if not folder.is_dir():
        p.error(f'Folder not found: {folder}')
    try:
        folder.resolve().relative_to(root.resolve())
    except ValueError:
        p.error(f'Copy the files into the archive first (e.g. {root}/Phone-Originals/{args.label}/), then catalog them there')
    from zoneinfo import ZoneInfo
    tzname = args.timezone or cfg.get('timezone')
    reference = ZoneInfo(tzname) if tzname else None
    rules = []
    for item in list(cfg.get('zone_until', [])) + args.zone_until:
        day, _, zone = item.partition('=')
        try:
            rules.append((date.fromisoformat(day.strip()), ZoneInfo(zone.strip())))
        except Exception:
            p.error(f'Bad --zone-until {item!r}; expected DATE=ZONE, e.g. 2026-11-02=America/New_York')
    rules.sort(key=lambda r: r[0])
    manifest = root / 'Metadata' / 'folder-import' / 'manifest.jsonl'
    s = catalog(folder, root, args.label, args.owner, manifest, args.since, args.until, reference, rules, args.dry_run)
    verb = 'Would catalog' if args.dry_run else 'Cataloged'
    kinds = ', '.join(f'{n} {k}s' for k, n in s['added'].items())
    print(f"{verb} {sum(s['added'].values())} files from {folder} as '{args.label}'" + (f' ({kinds})' if kinds else ''))
    print('Time taken from: ' + (', '.join(f'{k} {n}' for k, n in s['time_sources'].most_common()) or '-'))
    labels = {'recorded-offset': 'offset recorded in the photo', 'utc': 'UTC in the file',
              'zone-rule': '--zone-until rule', 'reference-zone': 'assumed already in reference zone'}
    ref_name = getattr(reference, 'key', None) or 'this machine\'s zone'
    print(f'Converted to {ref_name} using: '
          + (', '.join(f"{labels[k]} {n}" for k, n in s['zone_basis'].most_common()) or '-'))
    if s['time_sources'].get('file-modified'):
        print(f"  Note: {s['time_sources']['file-modified']} files had no camera time; their dates may be when they were copied.")
    extra = [f"replaced (file content changed, e.g. re-copied) {s['replaced']}" if s['replaced'] else '',
             f"already cataloged {s['already']}" if s['already'] else '',
             f"identical copies skipped {s['duplicates']}" if s['duplicates'] else '',
             f"outside the date range {s['out_of_range']}" if s['out_of_range'] else '',
             'other files ignored: ' + ', '.join(f'{k} {n}' for k, n in s['skipped'].items()) if s['skipped'] else '']
    if any(extra):
        print(' | '.join(e for e in extra if e))
    s['unreadable'].report()
    if not args.dry_run:
        print(f'Manifest: {manifest}\nNext: python -m media_archive.review build')
    return 0


if __name__ == '__main__':
    sys.exit(main())
