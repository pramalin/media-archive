"""Review imported media with readable names, and record what you delete.

  build    copy every imported file (once) into <archive_root>/Review/<YYYY-MM-DD>/ as
           2026-11-12_1555_Sender_Chat.jpg (documents go to Review/Documents/), dated by when
           it was first shared. Safe to re-run: adds only new imports, never re-adds deleted files.
  apply    record files you deleted from Review as rejected (renamed or moved files are still
           recognised). Later steps use only what you kept.
  status   counts of kept, deleted-not-yet-applied, and rejected files.
  restore [WHAT]  copy deleted files back into Review (applied or not) and clear their rejection.
           WHAT is a day folder (2026-11-11), a file name or a glob; without it, everything.
  dates    re-apply first-shared dates to files in Review (if a build could not set them).

The archive (<archive_root>/WhatsApp/Media) is never modified.
"""
import argparse
import fnmatch
import hashlib
import json
import os
import re
import shutil
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

INVALID = re.compile(r'[<>:"/\\|?*\x00-\x1f]')


def slug(text, limit=40):
    text = INVALID.sub('', text or 'unknown')
    text = re.sub(r'\s+', '-', text.strip()).strip('.-')
    return text[:limit].rstrip('.-') or 'unknown'


def sha256(path):
    h = hashlib.sha256()
    with path.open('rb') as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b''):
            h.update(chunk)
    return h.hexdigest()


def load_jsonl(path):
    if not path.exists():
        return []
    return [json.loads(l) for l in path.read_text(encoding='utf-8').splitlines() if l.strip()]


def load_json(path, default):
    return json.loads(path.read_text(encoding='utf-8')) if path.exists() else default


def save_json(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix('.tmp')
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding='utf-8')
    tmp.replace(path)


def set_time(path, stamp, warned=[]):
    """Best effort: drvfs mounts without uid= make files root-owned, and then utime is refused."""
    ts = stamp.timestamp()  # phone-local time interpreted in this machine's time zone
    try:
        os.utime(path, (ts, ts))
        return True
    except OSError as exc:
        if not warned:
            warned.append(exc)
            print(f'Note: cannot set file dates on this drive ({exc.strerror}); copying continues. '
                  'Remount with uid/gid (see docs/review.md) and run "review dates" to set them later.',
                  flush=True)
        return False


class Review:
    def __init__(self, root):
        self.root = Path(root)
        self.review = self.root / 'Review'
        # Every importer's manifest: WhatsApp exports and folders of phone/camera originals.
        self.manifests = [self.root / 'Metadata' / name / 'manifest.jsonl'
                          for name in ('whatsapp-import', 'folder-import', 'whatsapp-folder')]
        self.index_path = self.root / 'Metadata' / 'review' / 'index.json'
        self.rejected_path = self.root / 'Metadata' / 'review' / 'rejected.json'
        self.index = load_json(self.index_path, {})        # sha256 -> {path, ...}
        self.rejected = load_json(self.rejected_path, {})  # sha256 -> {path, rejected_at, ...}

    def unique_media(self):
        """One entry per file content: the earliest share (original poster), plus all sources."""
        by_hash = {}
        for r in (row for m in self.manifests for row in load_jsonl(m)):
            entry = by_hash.setdefault(r['sha256'], {'sha256': r['sha256'], 'local_path': r['local_path'],
                                                     'sources': [], 'first': r})
            entry['sources'].append({k: r.get(k) for k in ('chat', 'sender', 'shared_at_local', 'original_filename')})
            if r['shared_at_local'] < entry['first']['shared_at_local']:
                entry['first'] = r
        return by_hash

    def _target(self, first, used):
        stamp = datetime.fromisoformat(first['shared_at_local'])
        ext = Path(first['local_path']).suffix.lower()
        if first['media_type'] == 'document':
            folder = self.review / 'Documents'
            base = f"{stamp:%Y-%m-%d}_{slug(Path(first['original_filename']).stem, 80)}"
        else:
            folder = self.review / f'{stamp:%Y-%m-%d}'
            base = f"{stamp:%Y-%m-%d_%H%M}_{slug(first['sender'])}_{slug(first['chat'], 30)}"
        name, n = base + ext, 2
        while (folder / name).exists() or (folder / name).as_posix() in used:
            name, n = f'{base}_{n}{ext}', n + 1
        used.add((folder / name).as_posix())
        return folder / name, stamp

    def adopt_orphans(self, media):
        """Index files already in Review but not in the index (e.g. after an interrupted build)."""
        known = {v['path'] for v in self.index.values()} | {v['path'] for v in self.rejected.values()}
        adopted = 0
        if not self.review.exists():
            return 0
        for f in self.review.rglob('*'):
            rel = f.relative_to(self.review).as_posix() if f.is_file() else None
            if not rel or rel in known or f.name.startswith('.') or f.suffix in ('.ini', '.db'):
                continue
            h = sha256(f)
            if h in media and h not in self.index and h not in self.rejected:
                self.index[h] = {'path': rel, 'size': f.stat().st_size,
                                 'media_type': media[h]['first']['media_type'], 'sources': media[h]['sources']}
                adopted += 1
        return adopted

    def remove_outdated(self, media, dry_run=False):
        """Drop Review copies whose content no longer exists in any manifest (e.g. a half-copied
        phone file that was re-copied). Returns their Review paths."""
        outdated = [h for h in self.index if h not in media]
        if not outdated:
            return []
        present, _ = self.scan()
        removed = []
        for h in outdated:
            rel = present.get(h, self.index[h]['path'])
            removed.append(rel)
            if not dry_run:
                (self.review / rel).unlink(missing_ok=True)
                del self.index[h]
        if not dry_run:
            for h in [h for h in self.rejected if h not in media]:
                del self.rejected[h]
            save_json(self.index_path, self.index)
            save_json(self.rejected_path, self.rejected)
        return removed

    def build(self, dry_run=False):
        media = self.unique_media()
        outdated = self.remove_outdated(media, dry_run)
        adopted = 0 if dry_run else self.adopt_orphans(media)
        todo = [m for h, m in media.items() if h not in self.index and h not in self.rejected]
        todo.sort(key=lambda m: m['first']['shared_at_local'])
        need = sum((self.root / m['local_path']).stat().st_size for m in todo)
        if not dry_run and need > shutil.disk_usage(self.root).free:
            raise SystemExit(f'Not enough free space: need {need/1e9:.1f} GB')
        # Never reuse a name held by an indexed or rejected file (a deleted name must stay unambiguous).
        used = {(self.review / v['path']).as_posix() for d in (self.index, self.rejected) for v in d.values()}
        added = Counter()
        try:
            for m in todo:
                target, stamp = self._target(m['first'], used)
                added[target.parent.name] += 1
                if dry_run:
                    continue
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(self.root / m['local_path'], target)
                set_time(target, stamp)
                self.index[m['sha256']] = {'path': target.relative_to(self.review).as_posix(),
                                           'size': target.stat().st_size,
                                           'media_type': m['first']['media_type'], 'sources': m['sources']}
                if len(self.index) % 100 == 0:
                    save_json(self.index_path, self.index)
        finally:
            if not dry_run:
                save_json(self.index_path, self.index)  # an interrupted build resumes cleanly
        return {'added': added, 'bytes': need, 'adopted': adopted, 'outdated': outdated,
                'skipped_rejected': sum(h in self.rejected for h in media)}

    def dates(self):
        """(Re)apply first-shared dates to every file currently in Review."""
        media = self.unique_media()
        present, _ = self.scan()
        done = 0
        for h, rel in present.items():
            if h in media and set_time(self.review / rel, datetime.fromisoformat(media[h]['first']['shared_at_local'])):
                done += 1
        return done

    def scan(self):
        """Map review files to hashes: by indexed path first, hashing only unknown or moved files."""
        by_path = {v['path']: (h, v.get('size')) for h, v in self.index.items()}
        present, moved = {}, {}
        if self.review.exists():
            for f in self.review.rglob('*'):
                if not f.is_file() or f.name.startswith('.') or f.suffix in ('.ini', '.db'):
                    continue  # desktop.ini, Thumbs.db
                rel = f.relative_to(self.review).as_posix()
                known, size = by_path.get(rel, (None, None))
                h = known if known and size == f.stat().st_size else sha256(f)
                if h in self.index:
                    present[h] = rel
                    if rel != self.index[h]['path']:
                        moved[h] = rel
        return present, moved

    def apply(self):
        present, moved = self.scan()
        for h, rel in moved.items():
            self.index[h]['path'] = rel
        now = datetime.now(timezone.utc).isoformat()
        newly = [h for h in self.index if h not in present and h not in self.rejected]
        for h in newly:
            self.rejected[h] = {**self.index[h], 'rejected_at': now}
        save_json(self.index_path, self.index)
        save_json(self.rejected_path, self.rejected)
        return {'kept': len(present), 'rejected_now': len(newly), 'rejected_total': len(self.rejected),
                'moved': len(moved)}

    def status(self):
        present, _ = self.scan()
        pending = [h for h in self.index if h not in present and h not in self.rejected]
        media = self.unique_media()
        return {'kept': len(present), 'deleted_not_applied': len(pending), 'rejected': len(self.rejected),
                'not_in_review_yet': sum(h not in self.index and h not in self.rejected for h in media)}

    def restore(self, pattern=None):
        """Copy back deleted files (rejected, or deleted but not yet applied) whose Review path
        matches ``pattern`` (a folder like '2026-11-11', a file name, or a glob); all if None."""
        media = self.unique_media()
        present, _ = self.scan()
        candidates = {h: v for h, v in self.rejected.items()}
        candidates.update({h: v for h, v in self.index.items() if h not in present})
        restored = []
        for h, info in candidates.items():
            if pattern and not _matches(info['path'], pattern):
                continue
            target = self.review / info['path']
            if not target.exists() and h in media:
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(self.root / media[h]['local_path'], target)
                set_time(target, datetime.fromisoformat(media[h]['first']['shared_at_local']))
            self.index[h] = {k: v for k, v in info.items() if k != 'rejected_at'}
            self.rejected.pop(h, None)
            restored.append(info['path'])
        save_json(self.index_path, self.index)
        save_json(self.rejected_path, self.rejected)
        return sorted(restored)


def _matches(path, pattern):
    pattern = pattern.replace('\\', '/').strip('/')
    return (path == pattern or path.startswith(pattern + '/') or Path(path).name == pattern
            or fnmatch.fnmatch(path, pattern) or fnmatch.fnmatch(Path(path).name, pattern))


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument('command', choices=['build', 'apply', 'status', 'restore', 'dates'])
    p.add_argument('--config', default='config/whatsapp.local.json')
    p.add_argument('what', nargs='?', help='restore: day folder, file name or glob (default: all)')
    p.add_argument('--dry-run', action='store_true', help='build: report only')
    args = p.parse_args(argv)
    cfg = json.loads(Path(args.config).read_text(encoding='utf-8'))
    root = Path(cfg['archive_root']).expanduser()
    if not root.is_dir():
        p.error(f'Archive root not found: {root} (is the drive mounted?)')
    r = Review(root)
    if args.command == 'build':
        s = r.build(args.dry_run)
        total = sum(s['added'].values())
        print(f"{'Would copy' if args.dry_run else 'Copied'} {total} files ({s['bytes']/1e9:.2f} GB) into {r.review}"
              + (f" | {s['skipped_rejected']} rejected files left out" if s['skipped_rejected'] else ''))
        for folder, n in sorted(s['added'].items()):
            print(f'  {folder}: {n}')
        if s['outdated']:
            print(f"{'Would remove' if args.dry_run else 'Removed'} {len(s['outdated'])} outdated copies "
                  '(their source file was replaced, e.g. a re-copied phone file):')
            for rel in s['outdated'][:30]:
                print(f'  {rel}')
        if s['adopted']:
            print(f"Recognised {s['adopted']} files already in Review from an earlier, interrupted build.")
        if not total:
            print('Nothing new. Review already contains every kept file.')
    elif args.command == 'apply':
        s = r.apply()
        print(f"Kept: {s['kept']} | rejected now: {s['rejected_now']} | rejected in total: {s['rejected_total']}"
              + (f" | renamed/moved: {s['moved']}" if s['moved'] else ''))
    elif args.command == 'status':
        s = r.status()
        print(f"Kept: {s['kept']} | deleted, not yet applied: {s['deleted_not_applied']} | "
              f"rejected: {s['rejected']} | imported but not in Review yet: {s['not_in_review_yet']}")
    elif args.command == 'dates':
        print(f'Set dates on {r.dates()} files.')
    else:
        if args.what is None and r.rejected:
            print(f'Restoring all {len(r.rejected)} rejected files and any unapplied deletions.')
        restored = r.restore(args.what)
        print(f'Restored {len(restored)} files into {r.review}' + ('' if restored else
              f" (nothing deleted matches {args.what!r})" if args.what else ''))
        for path in restored[:50]:
            print(f'  {path}')
    return 0


if __name__ == '__main__':
    sys.exit(main())
