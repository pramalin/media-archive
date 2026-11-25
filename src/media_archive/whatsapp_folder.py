"""Catalog files from the phone's WhatsApp media folders and trace them back to their messages.

On Android, received and sent media live in
  Internal storage/Android/media/com.whatsapp/WhatsApp/Media/WhatsApp Images (and WhatsApp Video,
  WhatsApp Documents; each with a Sent subfolder)
with names like IMG-20261115-WA0042.jpg. Chat exports made on the same phone refer to the very same
names, so each file is matched to its chat, sender and time through every export ZIP in
<archive_root>/Chat-Exports. Copy the folders into the archive (e.g.
<archive_root>/Phone-Originals/WhatsApp-Media/) and run this; files are cataloged in place.

  - identical to a file already imported from an export: skipped
  - name found in an export: cataloged with that chat, sender and time
  - not found (chat not exported): cataloged as "chat unknown", dated from the file name;
    the report lists them by date with any not-downloaded media in the exports on that day
"""
import argparse
import json
import sys
import zipfile
from collections import Counter, defaultdict
from datetime import date, datetime, timezone
from pathlib import Path

from .folder_import import Unreadable, sha256
from .whatsapp_import import (FILE_DATE, attachments, chat_name, chat_settings, detect_date_order, media_type,
                              parse_android)

UNKNOWN_CHAT = 'WhatsApp (chat unknown)'
UNKNOWN_SENDER = 'Unknown'


def export_index(exports_dir, cfg):
    """file name -> [occurrences], and date -> Counter of not-downloaded media per (chat, sender)."""
    by_name, omitted = defaultdict(list), defaultdict(Counter)
    for zip_path in sorted(Path(exports_dir).rglob('*.zip')):
        if not zipfile.is_zipfile(zip_path):
            continue
        with zipfile.ZipFile(zip_path) as z:
            txt = [n for n in z.namelist() if n.lower().endswith('.txt') and '/' not in n.strip('/')]
            if len(txt) != 1:
                continue
            messages = parse_android(z.read(txt[0]).decode('utf-8-sig'))
        chat = chat_name(zip_path)
        _, aliases = chat_settings(cfg, chat)
        for item in attachments(messages, detect_date_order(messages)):
            person = aliases.get(item['sender'], item['sender'])
            if item['kind'] == 'omitted':
                omitted[item['shared_at_local'].date()][(chat, person)] += 1
                continue
            by_name[item['original_filename']].append({
                'chat': chat, 'sender': person, 'sender_raw': item['sender'],
                'shared_at_local': item['shared_at_local'], 'caption': item['caption'],
                'export_zip': zip_path.name, 'export_line': item['export_line']})
    return by_name, omitted


def name_date(name, path, tz=None):
    """Date from IMG-YYYYMMDD-WA…; the file's modified time supplies the time when it is that day."""
    m = FILE_DATE.match(name)
    if not m:
        return None, None
    day = datetime.strptime(m[1], '%Y%m%d')
    modified = datetime.fromtimestamp(path.stat().st_mtime, tz).replace(tzinfo=None)
    if modified.date() == day.date():
        return modified.replace(microsecond=0), 'file-modified'
    return day, 'day'


def catalog(folder, root, exports_dir, cfg, manifest, since=None, until=None, types=('photo', 'video'),
            dry_run=False, tz=None):
    by_name, omitted = export_index(exports_dir, cfg)
    archived = set()  # every hash already cataloged by any importer
    for m in (root / 'Metadata' / n / 'manifest.jsonl' for n in ('whatsapp-import', 'folder-import', 'whatsapp-folder')):
        if m.exists():
            for line in m.read_text(encoding='utf-8').splitlines():
                row = json.loads(line)
                if (root / row['local_path']).is_file():
                    archived.add(row['sha256'])
    unreadable = Unreadable()
    s = {'unreadable': unreadable, 'matched': Counter(), 'unmatched_by_day': Counter(), 'already': 0, 'duplicates': 0,
         'out_of_range': 0, 'skipped': Counter(), 'rows': []}
    seen = set()
    now = datetime.now(timezone.utc).isoformat()
    for path in sorted(p for p in Path(folder).rglob('*') if p.is_file()):
        kind = media_type(path.name)
        if kind not in types or path.name.startswith('.'):
            s['skipped'][kind] += 1
            continue
        rel = path.resolve().relative_to(root.resolve()).as_posix()
        hits = by_name.get(path.name, [])
        if hits:
            when = min(h['shared_at_local'] for h in hits)
        else:
            when, _ = name_date(path.name, path, tz)
            if when is None:
                when = datetime.fromtimestamp(path.stat().st_mtime, tz).replace(tzinfo=None, microsecond=0)
        if (since and when.date() < since) or (until and when.date() > until):
            s['out_of_range'] += 1
            continue
        try:
            digest = sha256(path)
            unreadable.ok()
        except OSError as exc:
            unreadable.add(path, exc)
            continue
        if digest in archived or digest in seen:
            s['already' if digest in archived else 'duplicates'] += 1
            continue
        seen.add(digest)
        base = {'media_type': kind, 'original_filename': path.name, 'source': 'whatsapp-media-folder',
                'local_path': rel, 'sha256': digest, 'size_bytes': path.stat().st_size, 'imported_at': now,
                'sent_by_me': 'Sent' in path.relative_to(folder).parts}
        if hits:
            for h in hits:  # one row per chat the file was posted in (forwards reuse the file)
                s['matched'][h['chat']] += 1
                s['rows'].append({**base, 'chat': h['chat'], 'sender': h['sender'], 'sender_raw': h['sender_raw'],
                                  'shared_at_local': h['shared_at_local'].isoformat(timespec='minutes'),
                                  'timestamp_precision': 'minute', 'caption': h['caption'],
                                  'match': 'export-file-name', 'export_zip': h['export_zip'],
                                  'export_line': h['export_line']})
        else:
            stamp, precision = name_date(path.name, path, tz)
            stamp = stamp or when
            s['unmatched_by_day'][stamp.date()] += 1
            s['rows'].append({**base, 'chat': UNKNOWN_CHAT, 'sender': UNKNOWN_SENDER, 'sender_raw': None,
                              'shared_at_local': stamp.isoformat(timespec='minutes'),
                              'timestamp_precision': precision or 'file-modified', 'caption': None,
                              'match': 'none'})
    s['omitted'] = omitted
    if not dry_run and s['rows']:
        manifest.parent.mkdir(parents=True, exist_ok=True)
        with manifest.open('a', encoding='utf-8') as f:
            for row in s['rows']:
                f.write(json.dumps(row, ensure_ascii=False) + '\n')
    return s


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument('folder', help="Copy of the phone's WhatsApp media folder(s), inside the archive root")
    p.add_argument('--exports', help='Folder of chat export ZIPs (default: <archive_root>/Chat-Exports)')
    p.add_argument('--config', default='config/whatsapp.local.json')
    p.add_argument('--since', type=date.fromisoformat)
    p.add_argument('--until', type=date.fromisoformat)
    p.add_argument('--types', default='photo,video', help='Comma list: photo,video,document,audio,sticker,other')
    p.add_argument('--by-day', action='store_true', help='List unknown-chat files per day even over long ranges')
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
        p.error(f'Copy the folder into the archive first (e.g. {root}/Phone-Originals/WhatsApp-Media/)')
    exports = Path(args.exports) if args.exports else root / 'Chat-Exports'
    manifest = root / 'Metadata' / 'whatsapp-folder' / 'manifest.jsonl'
    tz = None
    if cfg.get('timezone'):
        from zoneinfo import ZoneInfo
        tz = ZoneInfo(cfg['timezone'])
    s = catalog(folder, root, exports, cfg, manifest, args.since, args.until,
                tuple(t.strip() for t in args.types.split(',')), args.dry_run, tz)

    files = len({r['sha256'] for r in s['rows']})
    unmatched = sum(s['unmatched_by_day'].values())
    print(f"{'Would catalog' if args.dry_run else 'Cataloged'} {files} new files: "
          f'{files - unmatched} traced to a message, {unmatched} with chat unknown')
    if s['already'] or s['duplicates'] or s['out_of_range']:
        print(f"Already in the archive: {s['already']} | identical copies in this folder: {s['duplicates']}"
              f" | outside the date range: {s['out_of_range']}")
    if s['matched']:
        print('Traced (entries per chat; a forwarded file counts once per chat):')
        for chat, n in s['matched'].most_common():
            print(f'  {n:5d}  {chat}')
    if unmatched and len(s['unmatched_by_day']) > 31 and not args.by_day:
        months, hints = Counter(), Counter()
        for day, n in s['unmatched_by_day'].items():
            months[day.strftime('%Y-%m')] += n
        for day, c in s['omitted'].items():
            hints[day.strftime('%Y-%m')] += sum(c.values())
        print('Chat unknown, by month (use --by-day for detail; shared in chats you have not exported):')
        for month, n in sorted(months.items()):
            print(f'  {month}: {n:5d}' + (f'   (not downloaded in exports that month: {hints[month]})' if hints[month] else ''))
    elif unmatched:
        print('Chat unknown, by day (shared in chats you have not exported):')
        for day, n in sorted(s['unmatched_by_day'].items()):
            hint = s['omitted'].get(day)
            extra = ''
            if hint:
                extra = '   not downloaded in exports that day: ' + ', '.join(
                    f'{c} / {who} x{k}' for (c, who), k in hint.most_common(3))
            print(f'  {day}: {n}{extra}')
    s['unreadable'].report()
    if not args.dry_run:
        print(f'Manifest: {manifest}\nNext: python -m media_archive.review build')
    return 0


if __name__ == '__main__':
    sys.exit(main())
