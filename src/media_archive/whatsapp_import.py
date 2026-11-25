"""Import photos and videos from a WhatsApp "Export chat" (Include media) ZIP made on Android.

Each attachment keeps its provenance: sender, shared time (minute precision, phone-local),
original WhatsApp file name and caption. Files are stored once under their SHA-256 hash.
Optionally reconciles against the text-only export from WhatsApp Web, which lists every
photo/video with a timestamp to the second.
"""
import argparse
import hashlib
import json
import re
import shutil
import sys
import uuid
import zipfile
from collections import Counter
from datetime import date, datetime, timezone
from pathlib import Path

# Android: "2/13/26, 11:47 PM - Sender: IMG-20260213-WA0010.jpg (file attached)"
# Newer Android versions put a narrow no-break space before AM/PM; some lines start with U+200E.
_SPACE = r'[\s  ]'
ANDROID_LINE = re.compile(
    r'^‎?(\d{1,2})/(\d{1,2})/(\d{2,4}),' + _SPACE + r'+(\d{1,2}):(\d{2})(?:' + _SPACE + r'*([AaPp])\.?[Mm]\.?)?'
    + _SPACE + r'+-' + _SPACE + r'+(.*)$')
ATTACHED = re.compile(r'^‎?(?P<name>[^/\\]+?\.[A-Za-z0-9]{2,5})\s+\(file attached\)\s*$')
OMITTED = re.compile(r'<Media omitted>|<[^>]+ omitted>|\bomitted\b', re.I)
# WhatsApp Web export: "[11/12/26, 3:55:54 PM] Sender: [Forwarded] <image omitted>"
WEB_LINE = re.compile(r'^‎?\[(\d{1,2})/(\d{1,2})/(\d{2,4}),' + _SPACE + r'+(\d{1,2}):(\d{2}):\d{2}(?:' + _SPACE
                      + r'*([AaPp])[Mm])?\]' + _SPACE + r'+([^:]+?):' + _SPACE + r'+(.*)$')
WEB_MEDIA = re.compile(r'<(image|video) omitted>', re.I)

PHOTO_EXT = {'.jpg', '.jpeg', '.png', '.webp', '.heic', '.heif', '.gif', '.bmp', '.avif'}
VIDEO_EXT = {'.mp4', '.3gp', '.mov', '.mkv', '.webm', '.m4v'}
# Android keeps a shared document's original name (e.g. 'Invitation.pdf'), so classify by extension.
DOCUMENT_EXT = {'.pdf', '.doc', '.docx', '.xls', '.xlsx', '.ppt', '.pptx', '.odt', '.ods', '.odp', '.rtf', '.csv'}
AUDIO_EXT = {'.opus', '.mp3', '.m4a', '.aac', '.ogg', '.wav'}
PREFIX_TYPES = {'IMG': 'photo', 'VID': 'video', 'STK': 'sticker', 'PTT': 'audio', 'AUD': 'audio', 'DOC': 'document'}
FILE_DATE = re.compile(r'^[A-Z]{3}-(\d{8})-WA\d+', re.I)


def media_type(name):
    """Sticker / voice-note prefixes win (stickers are .webp); otherwise the extension decides."""
    prefix = name[:3].upper() if name[3:4] == '-' else ''
    if prefix in ('STK', 'PTT', 'AUD'):
        return PREFIX_TYPES[prefix]
    ext = Path(name).suffix.lower()
    for kind, exts in (('photo', PHOTO_EXT), ('video', VIDEO_EXT), ('document', DOCUMENT_EXT), ('audio', AUDIO_EXT)):
        if ext in exts:
            return kind
    if ext == '.vcf':
        return 'contact'
    return PREFIX_TYPES.get(prefix, 'other')


def _to_datetime(order, a, b, y, hh, mm, ampm):
    month, day = (a, b) if order == 'mdy' else (b, a)
    year = y + 2000 if y < 100 else y
    hour = hh
    if ampm:
        hour = hh % 12 + (12 if ampm.lower() == 'p' else 0)
    return datetime(year, month, day, hour, mm)


def parse_android(text):
    """Return messages as dicts with the raw date fields; the date order is resolved later."""
    messages = []
    for number, line in enumerate(text.splitlines(), 1):
        m = ANDROID_LINE.match(line)
        if m:
            a, b, y, hh, mm = (int(m[i]) for i in range(1, 6))
            body = m[7]
            sender, sep, content = body.partition(': ')
            if not sep:  # System message ("X added Y", encryption notice ...).
                sender, content = None, body
            messages.append({'line': number, 'fields': (a, b, y, hh, mm, m[6]), 'sender': sender,
                             'content': content, 'continuation': []})
        elif messages:
            messages[-1]['continuation'].append(line)
    return messages


def detect_date_order(messages):
    """Choose month/day vs day/month using the dates embedded in IMG-YYYYMMDD-WA file names."""
    votes = Counter()
    for msg in messages:
        att = ATTACHED.match(msg['content'] or '')
        fd = FILE_DATE.match(att['name']) if att else None
        if not fd:
            continue
        a, b, y, *_ = msg['fields']
        year = y + 2000 if y < 100 else y
        for order in ('mdy', 'dmy'):
            month, day = (a, b) if order == 'mdy' else (b, a)
            if f'{year:04d}{month:02d}{day:02d}' == fd[1]:
                votes[order] += 1
    if not votes:
        return 'dmy' if any(m['fields'][0] > 12 for m in messages) else 'mdy'
    return votes.most_common(1)[0][0]


def attachments(messages, order):
    out = []
    for msg in messages:
        if msg['sender'] is None:
            continue
        stamp = _to_datetime(order, *msg['fields'])
        att = ATTACHED.match(msg['content'])
        if att:
            caption = '\n'.join(msg['continuation']).strip()
            out.append({'kind': 'attached', 'sender': msg['sender'], 'shared_at_local': stamp,
                        'original_filename': att['name'], 'media_type': media_type(att['name']),
                        'caption': caption or None, 'export_line': msg['line']})
        elif OMITTED.search(msg['content']):
            out.append({'kind': 'omitted', 'sender': msg['sender'], 'shared_at_local': stamp,
                        'export_line': msg['line']})
    return out


def parse_web(text, order='mdy'):
    """(sender, minute, type) for every photo/video in a WhatsApp Web text export."""
    rows = []
    for line in text.splitlines():
        m = WEB_LINE.match(line)
        if not m:
            continue
        media = WEB_MEDIA.search(m[8])
        if media:
            a, b, y, hh, mm = (int(m[i]) for i in range(1, 6))
            rows.append((m[7], _to_datetime(order, a, b, y, hh, mm, m[6]), 'photo' if media[1].lower() == 'image' else 'video'))
    return rows


def read_text(path):
    """Chat text from an export ZIP (the single top-level .txt) or a plain text file."""
    path = Path(path)
    if zipfile.is_zipfile(path):
        with zipfile.ZipFile(path) as z:
            names = [n for n in z.namelist() if n.lower().endswith('.txt') and '/' not in n.strip('/')]
            if len(names) != 1:
                raise ValueError(f'Expected one chat .txt in {path.name}, found {names}')
            return z.read(names[0]).decode('utf-8-sig')
    return path.read_text(encoding='utf-8-sig')


def store(src, suffix, dest):
    """Copy a stream into dest as <sha256><suffix>; identical bytes are stored once."""
    tmp = dest / (uuid.uuid4().hex + '.part')
    h, size = hashlib.sha256(), 0
    try:
        with tmp.open('wb') as out:
            for chunk in iter(lambda: src.read(1024 * 1024), b''):
                h.update(chunk); size += len(chunk); out.write(chunk)
        if not size:
            raise ValueError('empty file')
        digest = h.hexdigest()
        final = dest / (digest + suffix)
        if not final.exists():
            tmp.replace(final)
        return final, digest, size
    finally:
        tmp.unlink(missing_ok=True)


def record_key(row):
    return (row['chat'], row['sender_raw'], row['shared_at_local'], row['original_filename'])


def chat_name(zip_path):
    """'WhatsApp Chat with Family Group.zip' -> 'Family Group'."""
    stem = Path(zip_path).stem
    stem = re.sub(r'\s*\(\d+\)$', '', stem)  # 'name (1)' from repeated downloads
    return re.sub(r'^WhatsApp Chat (with|-)\s*', '', stem).strip() or stem


def load_manifest(manifest, root):
    """Keys of records whose file still exists, and every hash already stored."""
    done, hashes = set(), set()
    if manifest.exists():
        for line in manifest.read_text(encoding='utf-8').splitlines():
            row = json.loads(line)
            if (root / row['local_path']).is_file():
                done.add(record_key(row))
                hashes.add(row['sha256'])
    return done, hashes


def import_export(zip_path, chat, root, dest, manifest, since=None, until=None, sender=None,
                  types=('photo', 'video'), aliases=None, dry_run=False, order=None):
    """Import matching attachments; returns a summary dict (also used by the report)."""
    aliases = aliases or {}
    with zipfile.ZipFile(zip_path) as z:
        txt = [n for n in z.namelist() if n.lower().endswith('.txt') and '/' not in n.strip('/')]
        if len(txt) != 1:
            raise ValueError(f'Expected one chat .txt in the export, found {txt}')
        messages = parse_android(z.read(txt[0]).decode('utf-8-sig'))
        order = order or detect_date_order(messages)
        members = {Path(n).name: n for n in z.namelist() if not n.endswith('/')}
        done, hashes = load_manifest(manifest, root)
        summary = {'chat': chat, 'date_order': order, 'imported': Counter(), 'already': 0, 'duplicates': 0,
                   'missing': [], 'omitted': Counter(), 'skipped_types': Counter(), 'records': []}

        def wanted(item):
            day = item['shared_at_local'].date()
            name = aliases.get(item['sender'], item['sender'])
            return ((not since or day >= since) and (not until or day <= until)
                    and (not sender or sender in (item['sender'], name)))

        for item in attachments(messages, order):
            if not wanted(item):
                continue
            person = aliases.get(item['sender'], item['sender'])
            if item['kind'] == 'omitted':
                summary['omitted'][person] += 1
                continue
            if item['media_type'] not in types:
                summary['skipped_types'][item['media_type']] += 1
                continue
            row = {'chat': chat, 'sender': person, 'sender_raw': item['sender'],
                   'shared_at_local': item['shared_at_local'].isoformat(timespec='minutes'),
                   'timestamp_precision': 'minute', 'media_type': item['media_type'],
                   'original_filename': item['original_filename'], 'caption': item['caption'],
                   'source': 'whatsapp-export', 'export_zip': Path(zip_path).name,
                   'export_line': item['export_line']}
            if record_key(row) in done:
                summary['already'] += 1
                continue
            member = members.get(item['original_filename'])
            if not member:
                summary['missing'].append(row)
                continue
            if dry_run:
                summary['imported'][(person, item['media_type'])] += 1
                continue
            suffix = Path(item['original_filename']).suffix.lower()
            with z.open(member) as src:
                final, digest, size = store(src, suffix, dest)
            row.update(local_path=final.relative_to(root).as_posix(), sha256=digest, size_bytes=size,
                       imported_at=datetime.now(timezone.utc).isoformat())
            if digest in hashes:
                summary['duplicates'] += 1
            hashes.add(digest)
            with manifest.open('a', encoding='utf-8') as f:
                f.write(json.dumps(row, ensure_ascii=False) + '\n')
            done.add(record_key(row))
            summary['records'].append(row)
            summary['imported'][(person, item['media_type'])] += 1
    return summary


def reconcile(phone_rows, web_rows, aliases=None, me=None):
    """Compare (sender, minute, type) counts: phone export (attached + omitted) vs web export."""
    aliases = aliases or {}
    def name(s):
        if me and s == 'You':
            s = me
        return aliases.get(s, s)
    phone = Counter((name(s), t, k) for s, t, k in phone_rows)
    web = Counter((name(s), t, k) for s, t, k in web_rows)
    only_web = web - phone
    only_phone = phone - web
    return {'web_total': sum(web.values()), 'phone_total': sum(phone.values()),
            'only_web': only_web, 'only_phone': only_phone}


def _print_summary(s, dry_run):
    verb = 'Would import' if dry_run else 'Imported'
    total = sum(s['imported'].values())
    print(f"Date order: {s['date_order']} | {verb}: {total} | already recorded: {s['already']}"
          + ('' if dry_run else f" | identical to an earlier file: {s['duplicates']}"))
    for (person, kind), n in sorted(s['imported'].items(), key=lambda x: -x[1]):
        print(f'  {n:5d}  {person} | {kind}')
    if s['skipped_types']:
        print('Skipped types (use --types to include): ' + ', '.join(f'{k} {v}' for k, v in s['skipped_types'].items()))
    if s['missing']:
        print(f"{len(s['missing'])} attachments are named in the chat but absent from the ZIP:")
        for r in s['missing'][:30]:
            print(f"  {r['shared_at_local']}  {r['sender']}  {r['original_filename']}")
    if s['omitted']:
        print(f"{sum(s['omitted'].values())} media were not downloaded on the phone (\"omitted\") - by sender:")
        for person, n in s['omitted'].most_common():
            print(f'  {n:5d}  {person}')


def chat_settings(cfg, chat):
    """Per-chat since/aliases from a matching 'groups' entry, falling back to top-level values."""
    entry = next((g for g in cfg.get('groups', []) if g.get('name') == chat), {})
    since = entry.get('since', cfg.get('since'))
    aliases = {**cfg.get('aliases', {}), **entry.get('aliases', {})}
    return (date.fromisoformat(since) if since else None), aliases


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument('zips', nargs='+', metavar='ZIP', help='Android "Export chat" ZIPs (Include media)')
    p.add_argument('--config', default='config/whatsapp.local.json')
    p.add_argument('--since', type=date.fromisoformat,
                   help="Overrides the config: a matching group's 'since', else the top-level 'since'")
    p.add_argument('--until', type=date.fromisoformat)
    p.add_argument('--sender', help='Exact sender (as in the export, or its alias)')
    p.add_argument('--types', default='photo,video', help='Comma list: photo,video,document,audio,sticker,contact,other')
    p.add_argument('--date-order', choices=['mdy', 'dmy'], help='Default: detected from file names')
    p.add_argument('--check', metavar='WEB_EXPORT', help='WhatsApp Web text export (.zip or .txt) to reconcile against (one ZIP only)')
    p.add_argument('--me', help='Your name as it appears in the phone export (the web export says "You")')
    p.add_argument('--dry-run', action='store_true', help='Report only; copy nothing')
    args = p.parse_args(argv)

    cfg = json.loads(Path(args.config).read_text(encoding='utf-8'))
    root = Path(cfg['archive_root']).expanduser()
    if not root.is_dir():
        p.error(f'Archive root not found: {root} (is the drive mounted?)')
    zips = [Path(z) for z in args.zips]
    for z in zips:
        if not zipfile.is_zipfile(z):
            p.error(f'Not a ZIP file: {z}')
    if args.check and len(zips) != 1:
        p.error('--check compares one phone export with its web export; give a single ZIP')
    dest = root / 'WhatsApp' / 'Media'
    manifest = root / 'Metadata' / 'whatsapp-import' / 'manifest.jsonl'
    types = tuple(t.strip() for t in args.types.split(','))
    if not args.dry_run:
        dest.mkdir(parents=True, exist_ok=True)
        manifest.parent.mkdir(parents=True, exist_ok=True)
        need = 0
        for z in zips:
            with zipfile.ZipFile(z) as zf:
                need += sum(i.file_size for i in zf.infolist())
        free = shutil.disk_usage(root).free
        if free < need:
            p.error(f'Not enough free space on {root}: need up to {need/1e9:.1f} GB, have {free/1e9:.1f} GB')

    totals = Counter()
    for z in zips:
        chat = chat_name(z)
        cfg_since, aliases = chat_settings(cfg, chat)
        since = args.since or cfg_since
        print(f"\n=== {chat} | from {since or 'the beginning'}" + (f' to {args.until}' if args.until else '')
              + (f' | sender {args.sender}' if args.sender else ''))
        s = import_export(z, chat, root, dest, manifest, since, args.until, args.sender, types,
                          aliases, args.dry_run, args.date_order)
        _print_summary(s, args.dry_run)
        totals.update(imported=sum(s['imported'].values()), already=s['already'], duplicates=s['duplicates'],
                      missing=len(s['missing']), omitted=sum(s['omitted'].values()))
        if args.check:
            _check(z, s, args, since, aliases)
    if len(zips) > 1:
        verb = 'would import' if args.dry_run else 'imported'
        print(f"\n=== All chats: {verb} {totals['imported']} | already recorded {totals['already']} | "
              f"missing from ZIPs {totals['missing']} | not downloaded {totals['omitted']}"
              + ('' if args.dry_run else f" | identical to a file already stored {totals['duplicates']}"))
    if not args.dry_run:
        print(f'Files: {dest}\nManifest: {manifest}')
    return 0


def _check(zip_path, s, args, since, aliases):
    with zipfile.ZipFile(zip_path) as z:
        txt = [n for n in z.namelist() if n.lower().endswith('.txt')][0]
        items = attachments(parse_android(z.read(txt).decode('utf-8-sig')), s['date_order'])
    def in_range(sender_, stamp):
        return ((not since or stamp.date() >= since) and (not args.until or stamp.date() <= args.until)
                and (not args.sender or args.sender in (sender_, aliases.get(sender_, sender_))))
    # Omitted lines don't say photo or video, so compare on (sender, minute) only.
    phone = [(i['sender'], i['shared_at_local'], 'media') for i in items
             if in_range(i['sender'], i['shared_at_local'])
             and (i['kind'] == 'omitted' or i['media_type'] in ('photo', 'video'))]
    web = [(s_, t, 'media') for s_, t, _ in parse_web(read_text(args.check), s['date_order']) if in_range(s_, t)]
    r = reconcile(phone, web, aliases, args.me)
    print(f"\nCheck against web export: web lists {r['web_total']} photos/videos, phone export has {r['phone_total']}.")
    if not r['only_web'] and not r['only_phone']:
        print('Every web-listed photo/video has a matching phone entry (same sender and minute).')
    for label, diff in (('Listed on web but not in phone export', r['only_web']),
                        ('In phone export but not on web', r['only_phone'])):
        if diff:
            print(f'{label}: {sum(diff.values())}')
            for (person, stamp, _), n in sorted(diff.items(), key=lambda x: x[0][1])[:30]:
                print(f'  {stamp:%Y-%m-%d %H:%M}  {person}  x{n}')


if __name__ == '__main__':
    sys.exit(main())
