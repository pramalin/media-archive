"""Tests for cataloging phone/camera originals (synthetic files only)."""
import contextlib
import io
import json
import struct
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from media_archive.folder_import import main, mp4_time, name_time, normalize
from media_archive.review import main as review_main

try:
    from PIL import Image
except ImportError:  # pragma: no cover
    Image = None


def jpeg(path, taken=None, colour=(200, 30, 30), offset=None):
    im = Image.new('RGB', (8, 8), colour)
    exif = Image.Exif()
    if taken:
        exif.get_ifd(0x8769)[36867] = taken
    if offset:
        exif.get_ifd(0x8769)[0x9011] = offset
    im.save(path, 'JPEG', exif=exif.tobytes())


def mp4(path, created_utc):
    secs = int((created_utc - datetime(1904, 1, 1, tzinfo=timezone.utc)).total_seconds())
    mvhd = struct.pack('>I4sB3sII', 20, b'mvhd', 0, b'\0\0\0', secs, secs)
    moov = struct.pack('>I4s', 8 + len(mvhd), b'moov') + mvhd
    path.write_bytes(struct.pack('>I4s', 16, b'ftyp') + b'isom\0\0\0\0' + moov)


@unittest.skipIf(Image is None, 'Pillow not installed')
class FolderImportTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory(); self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.cfg = self.root / 'cfg.json'
        self.cfg.write_text(json.dumps({'archive_root': str(self.root), 'timezone': 'Asia/Kolkata'}))
        self.src = self.root / 'Phone-Originals' / 'Padhu-Phone'
        (self.src / 'sub').mkdir(parents=True)
        jpeg(self.src / 'photo1.jpg', '2026:11:15 10:30:12')
        (self.src / 'sub' / 'photo1-copy.jpg').write_bytes((self.src / 'photo1.jpg').read_bytes())
        jpeg(self.src / 'IMG_20261115_183000.jpg', colour=(1, 2, 3))
        jpeg(self.src / 'PXL_20261115_050012345.jpg', colour=(4, 5, 6))
        jpeg(self.src / 'IMG_20250101_000000.jpg', colour=(7, 8, 9))
        (self.src / 'VID_20261115_190000.mp4').write_bytes(b'video')
        mp4(self.src / 'clip.mp4', datetime(2026, 11, 15, 14, 0, tzinfo=timezone.utc))
        (self.src / 'notes.txt').write_text('x')

    def run_main(self, *extra):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            main([str(self.src), '--label', 'Padhu-Phone', '--owner', 'Padhu Ramalingam',
                  '--config', str(self.cfg), '--since', '2026-11-01', *extra])
        return out.getvalue()

    def rows(self):
        m = self.root / 'Metadata' / 'folder-import' / 'manifest.jsonl'
        return {r['original_filename']: r for r in map(json.loads, m.read_text(encoding='utf-8').splitlines())}

    def test_time_sources(self):
        self.assertEqual(name_time('PXL_20261115_050012345.jpg'), datetime(2026, 11, 15, 5, 0, 12, tzinfo=timezone.utc))
        self.assertEqual(name_time('Screenshot_20261115-103012.png'), datetime(2026, 11, 15, 10, 30, 12))
        self.assertEqual(name_time('20261115_103012.jpg'), datetime(2026, 11, 15, 10, 30, 12))
        self.assertIsNone(name_time('photo1.jpg'))
        mp4(self.root / 'x.mp4', datetime(2026, 1, 2, 3, 4, 5, tzinfo=timezone.utc))
        self.assertEqual(mp4_time(self.root / 'x.mp4'), datetime(2026, 1, 2, 3, 4, 5, tzinfo=timezone.utc))

    def test_catalog_in_place(self):
        out = self.run_main()
        self.assertIn('Cataloged 5 files', out)
        self.assertIn('identical copies skipped 1', out)
        self.assertIn('outside the date range 1', out)
        self.assertIn('other files ignored: .txt 1', out)
        r = self.rows()
        self.assertEqual((r['photo1.jpg']['shared_at_local'], r['photo1.jpg']['time_source']), ('2026-11-15T10:30:12', 'exif'))
        self.assertEqual(r['IMG_20261115_183000.jpg']['time_source'], 'filename')
        self.assertEqual(r['PXL_20261115_050012345.jpg']['shared_at_local'], '2026-11-15T10:30:12')
        self.assertEqual((r['clip.mp4']['shared_at_local'], r['clip.mp4']['time_source']), ('2026-11-15T19:30:00', 'video-metadata'))
        self.assertEqual(r['photo1.jpg']['local_path'], 'Phone-Originals/Padhu-Phone/photo1.jpg')
        self.assertIn('already cataloged 6', self.run_main())  # re-run adds nothing (the copy counts too)
        self.assertEqual(len(self.rows()), 5)

    def test_review_includes_phone_originals(self):
        self.run_main()
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            review_main(['build', '--config', str(self.cfg)])
        self.assertIn('Copied 5 files', out.getvalue())
        day = self.root / 'Review' / '2026-11-15'
        names = sorted(f.name for f in day.iterdir())
        self.assertIn('2026-11-15_1030_Padhu-Ramalingam_Padhu-Phone.jpg', names)
        self.assertIn('2026-11-15_1930_Padhu-Ramalingam_Padhu-Phone.mp4', names)

    def test_us_then_india_times_line_up_with_whatsapp_ist(self):
        ist, ny = ZoneInfo('Asia/Kolkata'), ZoneInfo('America/New_York')
        rules = [(datetime(2026, 11, 2).date(), ny)]
        # 31 Oct 8 pm in the US (EDT, UTC-4) is 1 Nov 5:30 am IST.
        self.assertEqual(normalize(datetime(2026, 10, 31, 20, 0), 'exif', ist, rules),
                         (datetime(2026, 11, 1, 5, 30), datetime(2026, 10, 31, 20, 0), '-04:00', 'zone-rule:America/New_York'))
        # 1 Nov 8 pm: daylight saving has ended (EST, UTC-5) -> 2 Nov 6:30 am IST.
        self.assertEqual(normalize(datetime(2026, 11, 1, 20, 0), 'filename', ist, rules)[0], datetime(2026, 11, 2, 6, 30))
        # From 2 Nov the clock is already IST.
        self.assertEqual(normalize(datetime(2026, 11, 3, 10, 0), 'exif', ist, rules)[0:2],
                         (datetime(2026, 11, 3, 10, 0), datetime(2026, 11, 3, 10, 0)))
        # A recorded offset wins over the rule; a UTC video time is placed in the right local zone.
        recorded = datetime(2026, 11, 3, 9, 0, tzinfo=timezone(-timedelta(hours=5)))
        self.assertEqual(normalize(recorded, 'exif', ist, rules)[0], datetime(2026, 11, 3, 19, 30))
        utc = datetime(2026, 11, 1, 0, 0, tzinfo=timezone.utc)
        self.assertEqual(normalize(utc, 'video-metadata', ist, rules),
                         (datetime(2026, 11, 1, 5, 30), datetime(2026, 10, 31, 20, 0), '-04:00', 'utc'))

    def test_cli_zone_rule_and_recorded_offset(self):
        jpeg(self.src / 'us.jpg', '2026:10:31 20:00:00', colour=(9, 9, 9))
        jpeg(self.src / 'us-offset.jpg', '2026:10:31 21:00:00', colour=(8, 8, 8), offset='-04:00')
        self.run_main('--zone-until', '2026-11-02=America/New_York')
        r = self.rows()
        self.assertEqual((r['us.jpg']['shared_at_local'], r['us.jpg']['taken_local'], r['us.jpg']['taken_utc_offset']),
                         ('2026-11-01T05:30:00', '2026-10-31T20:00:00', '-04:00'))
        self.assertEqual((r['us-offset.jpg']['shared_at_local'], r['us-offset.jpg']['zone_basis']),
                         ('2026-11-01T06:30:00', 'recorded-offset'))
        self.assertEqual(r['photo1.jpg']['shared_at_local'], '2026-11-15T10:30:12')  # after the rule: unchanged

    def test_video_metadata_checked_against_file_name(self):
        from media_archive.folder_import import taken_at
        ist, rules = ZoneInfo('Asia/Kolkata'), [(datetime(2026, 11, 2).date(), ZoneInfo('America/New_York'))]
        good = self.root / 'VID_20261031_200000.mp4'; mp4(good, datetime(2026, 11, 1, 0, 1, tzinfo=timezone.utc))
        self.assertEqual(taken_at(good, 'video', ist, rules)[1], 'video-metadata')
        bad = self.root / 'VID_20261031_200001.mp4'; mp4(bad, datetime(2026, 10, 31, 20, 0, tzinfo=timezone.utc))  # local written as UTC
        self.assertEqual(taken_at(bad, 'video', ist, rules), (datetime(2026, 10, 31, 20, 0, 1), 'filename'))

    def test_recopy_replaces_half_copied_file_in_manifest_and_review(self):
        full = (self.src / 'photo1.jpg').read_bytes()
        (self.src / 'sub' / 'photo1-copy.jpg').unlink()
        (self.src / 'photo1.jpg').write_bytes(full[:len(full) // 2])  # interrupted copy: no readable EXIF
        self.run_main()
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            review_main(['build', '--config', str(self.cfg)])
        before = sorted(f.name for f in (self.root / 'Review').rglob('*.jpg'))
        (self.src / 'photo1.jpg').write_bytes(full)  # complete re-copy, same name
        out = self.run_main()
        self.assertIn('replaced (file content changed, e.g. re-copied) 1', out)
        rows = [json.loads(l) for l in (self.root / 'Metadata/folder-import/manifest.jsonl').read_text().splitlines()]
        self.assertEqual(len([r for r in rows if r['original_filename'] == 'photo1.jpg']), 1)
        self.assertEqual(self.rows()['photo1.jpg']['time_source'], 'exif')
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            review_main(['build', '--config', str(self.cfg)])
        self.assertIn('Removed 1 outdated copies', out.getvalue())
        self.assertIn('Copied 1 files', out.getvalue())
        after = sorted(f.name for f in (self.root / 'Review').rglob('*.jpg'))
        self.assertEqual(len(after), len(before))
        self.assertIn('2026-11-15_1030_Padhu-Ramalingam_Padhu-Phone.jpg', after)

    def test_folder_outside_archive_is_refused(self):
        with tempfile.TemporaryDirectory() as other, contextlib.redirect_stderr(io.StringIO()):
            with self.assertRaises(SystemExit):
                main([other, '--label', 'X', '--owner', 'Y', '--config', str(self.cfg)])


if __name__ == '__main__':
    unittest.main()
