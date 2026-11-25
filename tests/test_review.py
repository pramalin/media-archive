"""Tests for the review folder workflow, using a synthetic import (no real media)."""
import contextlib
import io
import json
import tempfile
import unittest
import zipfile
from datetime import datetime
from pathlib import Path

from media_archive.review import Review, main as review_main, slug
from media_archive.whatsapp_import import main as import_main


def export(path, lines, files):
    with zipfile.ZipFile(path, 'w') as z:
        z.writestr(Path(path).stem + '.txt', '\n'.join(lines))
        for name, data in files.items():
            z.writestr(name, data)
    return path


class ReviewTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory(); self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.cfg = self.root / 'cfg.json'
        self.cfg.write_text(json.dumps({'archive_root': str(self.root), 'since': '2026-11-01'}))
        self.ff = export(self.root / 'WhatsApp Chat with Family Friends!!!.zip', [
            '11/12/26, 3:55 PM - Vijaya Mahesh: IMG-20261112-WA0001.jpg (file attached)',
            '11/12/26, 3:55 PM - Vijaya Mahesh: IMG-20261112-WA0002.jpg (file attached)',
            '11/13/26, 9:00 AM - +1 (904) 900-9986: VID-20261113-WA0003.mp4 (file attached)',
            '11/14/26, 10:00 AM - Lavanya: Order of Ceremony.pdf (file attached)',
        ], {'IMG-20261112-WA0001.jpg': b'a', 'IMG-20261112-WA0002.jpg': b'b',
            'VID-20261113-WA0003.mp4': b'v', 'Order of Ceremony.pdf': b'%PDF'})
        self.fg = export(self.root / 'WhatsApp Chat with Family Group.zip', [
            '11/12/26, 6:00 PM - Anjali: IMG-20261112-WA0100.jpg (file attached)',  # forwarded copy of 'a'
        ], {'IMG-20261112-WA0100.jpg': b'a'})
        self.quiet(import_main, [str(self.ff), str(self.fg), '--config', str(self.cfg),
                                 '--types', 'photo,video,document'])

    def quiet(self, fn, argv):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            fn(argv)
        return out.getvalue()

    def review(self, *args):
        return self.quiet(review_main, [*args, '--config', str(self.cfg)])

    def files(self):
        rv = self.root / 'Review'
        return sorted(f.relative_to(rv).as_posix() for f in rv.rglob('*') if f.is_file())

    def test_slug(self):
        self.assertEqual(slug('Family Friends!!!'), 'Family-Friends!!!')
        self.assertEqual(slug('+1 (904) 900-9986'), '+1-(904)-900-9986')
        self.assertEqual(slug('a/b:c?'), 'abc')

    def test_build_names_dates_and_dedup(self):
        out = self.review('build')
        self.assertIn('Copied 4 files', out)  # 'a' appears in two chats but is copied once
        self.assertEqual(self.files(), [
            '2026-11-12/2026-11-12_1555_Vijaya-Mahesh_Family-Friends!!!.jpg',
            '2026-11-12/2026-11-12_1555_Vijaya-Mahesh_Family-Friends!!!_2.jpg',
            '2026-11-13/2026-11-13_0900_+1-(904)-900-9986_Family-Friends!!!.mp4',
            'Documents/2026-11-14_Order-of-Ceremony.pdf'])
        f = self.root / 'Review/2026-11-13/2026-11-13_0900_+1-(904)-900-9986_Family-Friends!!!.mp4'
        self.assertEqual(datetime.fromtimestamp(f.stat().st_mtime), datetime(2026, 11, 13, 9, 0))
        index = json.loads((self.root / 'Metadata/review/index.json').read_text(encoding='utf-8'))
        both = next(v for v in index.values() if len(v['sources']) == 2)
        self.assertEqual({s['chat'] for s in both['sources']}, {'Family Friends!!!', 'Family Group'})
        self.assertIn('Nothing new', self.review('build'))

    def test_delete_apply_rebuild_and_restore(self):
        self.review('build')
        rv = self.root / 'Review'
        (rv / '2026-11-12/2026-11-12_1555_Vijaya-Mahesh_Family-Friends!!!_2.jpg').unlink()
        self.assertIn('deleted, not yet applied: 1', self.review('status'))
        self.assertIn('Kept: 3 | rejected now: 1 | rejected in total: 1', self.review('apply'))
        # A new import must not bring the rejected file back, nor take its name.
        export(self.fg, ['11/12/26, 6:00 PM - Anjali: IMG-20261112-WA0100.jpg (file attached)',
                         '11/12/26, 3:55 PM - Anjali: IMG-20261112-WA0101.jpg (file attached)'],
               {'IMG-20261112-WA0100.jpg': b'a', 'IMG-20261112-WA0101.jpg': b'new', })
        self.quiet(import_main, [str(self.fg), '--config', str(self.cfg)])
        out = self.review('build')
        self.assertIn('Copied 1 files', out)
        self.assertIn('1 rejected files left out', out)
        self.assertIn('2026-11-12/2026-11-12_1555_Anjali_Family-Group.jpg', self.files())
        self.assertNotIn('2026-11-12/2026-11-12_1555_Vijaya-Mahesh_Family-Friends!!!_2.jpg', self.files())
        self.assertIn('Kept: 4 | rejected now: 0', self.review('apply'))
        self.assertIn('Restored 1 files', self.review('restore'))  # all
        self.assertEqual((rv / '2026-11-12/2026-11-12_1555_Vijaya-Mahesh_Family-Friends!!!_2.jpg').read_bytes(), b'b')
        self.assertIn('Kept: 5 | deleted, not yet applied: 0 | rejected: 0', self.review('status'))

    def test_renamed_and_moved_files_are_not_rejected(self):
        self.review('build')
        rv = self.root / 'Review'
        (rv / 'Favourites').mkdir()
        (rv / '2026-11-12/2026-11-12_1555_Vijaya-Mahesh_Family-Friends!!!.jpg').rename(rv / 'Favourites/best.jpg')
        out = self.review('apply')
        self.assertIn('Kept: 4 | rejected now: 0', out)
        self.assertIn('renamed/moved: 1', out)
        self.assertIn('Nothing new', self.review('build'))  # moved file is not copied again


    def test_date_failure_does_not_stop_build_and_interrupted_build_resumes(self):
        from unittest import mock
        import media_archive.review as rv
        rv.set_time.__defaults__[0].clear()
        calls = {'n': 0}
        real_copy = rv.shutil.copyfile
        def copy_then_crash(src, dst):
            calls['n'] += 1
            if calls['n'] == 3:
                raise KeyboardInterrupt  # simulate Ctrl+C / unplugged drive mid-build
            return real_copy(src, dst)
        with mock.patch.object(rv.os, 'utime', side_effect=PermissionError(1, 'Operation not permitted')), \
             mock.patch.object(rv.shutil, 'copyfile', side_effect=copy_then_crash):
            with self.assertRaises(KeyboardInterrupt):
                self.review('build')
        self.assertEqual(len(self.files()), 2)
        out = self.review('build')  # resumes: no duplicate copies
        self.assertIn('Copied 2 files', out)
        self.assertEqual(len(self.files()), 4)
        self.assertFalse(any('_3' in f for f in self.files()))
        self.assertIn('Set dates on 4 files', self.review('dates'))
        f = self.root / 'Review/2026-11-13/2026-11-13_0900_+1-(904)-900-9986_Family-Friends!!!.mp4'
        self.assertEqual(datetime.fromtimestamp(f.stat().st_mtime), datetime(2026, 11, 13, 9, 0))

    def test_orphan_left_by_crash_before_index_save_is_adopted(self):
        # The real failure: the first file was copied, then the build crashed before saving the index.
        self.review('build')
        (self.root / 'Metadata/review/index.json').unlink()
        out = self.review('build')
        self.assertIn('Recognised 4 files', out)
        self.assertIn('Nothing new', out)
        self.assertEqual(len(self.files()), 4)


    def test_restore_one_folder_only_applied_or_not(self):
        self.review('build')
        rv = self.root / 'Review'
        mp4 = '2026-11-13/2026-11-13_0900_+1-(904)-900-9986_Family-Friends!!!.mp4'
        jpg = '2026-11-12/2026-11-12_1555_Vijaya-Mahesh_Family-Friends!!!_2.jpg'
        (rv / jpg).unlink()
        self.review('apply')                      # intentional, applied deletion
        (rv / mp4).unlink()                       # mistaken deletion, not applied
        out = self.review('restore', '2026-11-13')
        self.assertIn('Restored 1 files', out)
        self.assertTrue((rv / mp4).exists())
        self.assertFalse((rv / jpg).exists())     # other deletions untouched
        self.assertIn('rejected: 1', self.review('status'))
        self.assertIn('nothing deleted matches', self.review('restore', '2026-12-01'))
        self.assertIn('Restored 1 files', self.review('restore', '*_2.jpg'))
        self.assertIn('rejected: 0', self.review('status'))


if __name__ == '__main__':
    unittest.main()
