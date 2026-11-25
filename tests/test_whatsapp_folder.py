"""Tests for tracing the phone's WhatsApp media folder back to export messages (synthetic data)."""
import contextlib
import io
import json
import os
import tempfile
import unittest
import zipfile
from datetime import date, datetime, timedelta
from pathlib import Path

from media_archive.review import main as review_main
from media_archive.whatsapp_folder import main
from media_archive.whatsapp_import import main as import_main


class WhatsAppFolderTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory(); self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.cfg = self.root / 'cfg.json'
        self.cfg.write_text(json.dumps({'archive_root': str(self.root), 'since': '2026-11-10',
                                        'aliases': {'+91 96566 09527': 'Ajee'}}))
        exports = self.root / 'Chat-Exports'; exports.mkdir()
        with zipfile.ZipFile(exports / 'WhatsApp Chat with Family Group.zip', 'w') as z:
            z.writestr('WhatsApp Chat with Family Group.txt', '\n'.join([
                '11/12/26, 6:00 PM - Anjali: IMG-20261112-WA0001.jpg (file attached)',   # in the export ZIP
                '11/12/26, 6:05 PM - Anjali: IMG-20261112-WA0002.jpg (file attached)',   # named, but older than since? no: missing from ZIP
                '11/01/26, 9:00 AM - Shanthi: IMG-20261101-WA0003.jpg (file attached)',  # before 'since'
                '11/14/26, 9:00 AM - Shanthi: <Media omitted>',
            ]))
            z.writestr('IMG-20261112-WA0001.jpg', b'one')
        with zipfile.ZipFile(exports / 'WhatsApp Chat with ATC.zip', 'w') as z:
            z.writestr('WhatsApp Chat with ATC.txt',
                       '11/12/26, 7:00 PM - +91 96566 09527: IMG-20261112-WA0002.jpg (file attached)')
        self.quiet(import_main, [str(exports / 'WhatsApp Chat with Family Group.zip'), '--config', str(self.cfg)])
        # The phone's media folder, copied to the SSD.
        self.media = self.root / 'Phone-Originals' / 'WhatsApp-Media' / 'WhatsApp Images'
        (self.media / 'Sent').mkdir(parents=True)
        (self.media / 'IMG-20261112-WA0001.jpg').write_bytes(b'one')       # already archived from the export
        (self.media / 'IMG-20261112-WA0002.jpg').write_bytes(b'two')       # posted in two chats, not in any ZIP
        (self.media / 'IMG-20261101-WA0003.jpg').write_bytes(b'three')     # traced, but before since
        unknown = self.media / 'IMG-20261114-WA0009.jpg'; unknown.write_bytes(b'nine')  # chat not exported
        t = datetime(2026, 11, 14, 21, 15).timestamp(); os.utime(unknown, (t, t))
        (self.media / 'Sent' / 'IMG-20261114-WA0010.jpg').write_bytes(b'ten')
        (self.media / 'IMG-20261114-WA0011.opus').write_bytes(b'voice')

    def quiet(self, fn, argv):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            fn(argv)
        return out.getvalue()

    def run_main(self, *extra):
        return self.quiet(main, [str(self.media.parent), '--config', str(self.cfg), '--since', '2026-11-10', *extra])

    def rows(self):
        m = self.root / 'Metadata' / 'whatsapp-folder' / 'manifest.jsonl'
        return [json.loads(l) for l in m.read_text(encoding='utf-8').splitlines()]

    def test_trace_and_report(self):
        out = self.run_main()
        self.assertIn('Cataloged 3 new files: 1 traced to a message, 2 with chat unknown', out)
        self.assertIn('Already in the archive: 1', out)
        self.assertIn('outside the date range: 1', out)
        self.assertIn('2026-11-14: 2   not downloaded in exports that day: Family Group / Shanthi x1', out)
        rows = self.rows()
        traced = [r for r in rows if r['original_filename'] == 'IMG-20261112-WA0002.jpg']
        self.assertEqual({(r['chat'], r['sender'], r['shared_at_local']) for r in traced},
                         {('Family Group', 'Anjali', '2026-11-12T18:05'), ('ATC', 'Ajee', '2026-11-12T19:00')})
        nine = next(r for r in rows if r['original_filename'] == 'IMG-20261114-WA0009.jpg')
        self.assertEqual((nine['chat'], nine['shared_at_local'], nine['timestamp_precision']),
                         ('WhatsApp (chat unknown)', '2026-11-14T21:15', 'file-modified'))
        ten = next(r for r in rows if r['original_filename'] == 'IMG-20261114-WA0010.jpg')
        self.assertTrue(ten['sent_by_me'])
        self.assertIn('Already in the archive: 4', self.run_main())  # re-run adds nothing

    def test_dry_run_writes_nothing_and_review_includes_traced_files(self):
        self.run_main('--dry-run')
        self.assertFalse((self.root / 'Metadata' / 'whatsapp-folder').exists())
        self.run_main()
        out = self.quiet(review_main, ['build', '--config', str(self.cfg)])
        self.assertIn('Copied 4 files', out)  # 1 from the export + 3 from the folder
        names = sorted(f.name for f in (self.root / 'Review').rglob('*.jpg'))
        self.assertIn('2026-11-12_1805_Anjali_Family-Group.jpg', names)  # earliest chat names the file
        self.assertIn('2026-11-14_2115_Unknown_WhatsApp-(chat-unknown).jpg', names)


    def test_long_ranges_are_summarised_by_month(self):
        for i in range(40):  # 40 different days in 2025
            day = date(2025, 1, 1) + timedelta(days=i * 3)
            (self.media / f'IMG-{day:%Y%m%d}-WA{i:04d}.jpg').write_bytes(f'old {i}'.encode())
        out = self.quiet(main, [str(self.media.parent), '--config', str(self.cfg), '--since', '2025-01-01', '--dry-run'])
        self.assertIn('Chat unknown, by month', out)
        self.assertIn('  2025-01:    11', out)
        self.assertNotIn('2025-01-04:', out)
        out = self.quiet(main, [str(self.media.parent), '--config', str(self.cfg), '--since', '2025-01-01', '--dry-run', '--by-day'])
        self.assertIn('2025-01-04: 1', out)


    def test_unreadable_file_is_skipped_and_listed(self):
        from unittest import mock
        import media_archive.whatsapp_folder as wf
        real = wf.sha256
        def flaky(path):
            if path.name == 'IMG-20261114-WA0009.jpg':
                raise OSError(5, 'Input/output error')
            return real(path)
        with mock.patch.object(wf, 'sha256', side_effect=flaky):
            out = self.run_main()
        self.assertIn('1 files could not be read and were skipped', out)
        self.assertIn('IMG-20261114-WA0009.jpg  (Input/output error)', out)
        self.assertIn('Cataloged 2 new files', out)
        self.assertIn('Cataloged 1 new files', self.run_main())  # readable again later: picked up

    def test_drive_gone_stops_without_recording(self):
        from unittest import mock
        import media_archive.whatsapp_folder as wf
        for i in range(25):
            (self.media / f'IMG-20261120-WA{i:04d}.jpg').write_bytes(f'x{i}'.encode())
        with mock.patch.object(wf, 'sha256', side_effect=OSError(5, 'Input/output error')):
            with self.assertRaises(SystemExit) as cm:
                self.run_main()
        self.assertIn('drive may have disconnected', str(cm.exception))
        self.assertFalse((self.root / 'Metadata' / 'whatsapp-folder').exists())


if __name__ == '__main__':
    unittest.main()
