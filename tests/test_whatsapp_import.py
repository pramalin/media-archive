"""Tests for the Android export importer, using a synthetic export (no real chat data)."""
import contextlib
import io
import json
import tempfile
import unittest
import zipfile
from datetime import date, datetime
from pathlib import Path

from media_archive.whatsapp_import import (attachments, chat_name, chat_settings, detect_date_order,
                                           import_export, main, parse_android, parse_web)

NNBSP = ' '
CHAT = '\n'.join([
    '1/15/26, 6:11 PM - Messages and calls are end-to-end encrypted.',
    '1/15/26, 6:11 PM - +1 (904) 504-3249: STK-20260115-WA0021.webp (file attached)',
    '11/10/26, 9:00 AM - Shanthi: IMG-20261110-WA0001.jpg (file attached)',
    f'11/12/26, 3:55{NNBSP}PM - Vijaya Mahesh: IMG-20261112-WA0001.jpg (file attached)',
    'Mehndi night',
    'second caption line',
    '11/12/26, 3:56 PM - Vijaya Mahesh: ‎IMG-20261112-WA0002.jpg (file attached)',
    '11/12/26, 4:00 PM - Shanthi: VID-20261112-WA0003.mp4 (file attached)',
    '11/12/26, 4:01 PM - Shanthi: IMG-20261112-WA0004.jpg (file attached)',
    '11/12/26, 4:02 PM - Uma Ravi: <Media omitted>',
    '11/13/26, 9:00 AM - Mahesh Kumar added Shanthi',
    '11/13/26, 9:05 AM - Mahesh Kumar: Lunch: at 1pm',
    '11/13/26, 9:06 AM - +1 (904) 900-9986: IMG-20261113-WA0005.jpg (file attached)',
])
FILES = {  # IMG-...0004 is deliberately absent; 0001 and 0002 have identical bytes.
    'STK-20260115-WA0021.webp': b'sticker',
    'IMG-20261110-WA0001.jpg': b'old photo',
    'IMG-20261112-WA0001.jpg': b'same bytes',
    'IMG-20261112-WA0002.jpg': b'same bytes',
    'VID-20261112-WA0003.mp4': b'video bytes',
    'IMG-20261113-WA0005.jpg': b'unsaved contact photo',
}
WEB = '\n'.join([
    '[11/12/26, 3:55:54 PM] Vijaya Mahesh: <image omitted>',
    '[11/12/26, 3:56:10 PM] Vijaya Mahesh: [Forwarded] <image omitted>',
    '[11/12/26, 4:00:01 PM] Shanthi: <video omitted>',
    '[11/12/26, 4:01:30 PM] Shanthi: <image omitted>',
    '[11/12/26, 4:02:00 PM] Uma Ravi: <image omitted>',
    '[11/13/26, 9:06:12 AM] +1 (904) 900-9986: <image omitted>',
    '[11/14/26, 8:00:00 AM] KRISH: <image omitted>',
])


def make_zip(path, chat=CHAT, files=FILES):
    with zipfile.ZipFile(path, 'w') as z:
        z.writestr('WhatsApp Chat with Test Group.txt', chat)
        for name, data in files.items():
            z.writestr(name, data)
    return path


class ImportTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory(); self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.zip = make_zip(self.root / 'WhatsApp Chat with Test Group.zip')
        self.dest = self.root / 'WhatsApp' / 'G'; self.dest.mkdir(parents=True)
        self.manifest = self.root / 'Metadata' / 'm.jsonl'; self.manifest.parent.mkdir()

    def run_import(self, **kw):
        kw.setdefault('since', date(2026, 11, 11))
        return import_export(self.zip, 'Test Group', self.root, self.dest, self.manifest, **kw)

    def rows(self):
        return [json.loads(l) for l in self.manifest.read_text(encoding='utf-8').splitlines()]

    def test_parse_formats_captions_and_system_lines(self):
        msgs = parse_android(CHAT)
        self.assertEqual(detect_date_order(msgs), 'mdy')
        items = attachments(msgs, 'mdy')
        attached = [i for i in items if i['kind'] == 'attached']
        self.assertEqual(len(attached), 7)
        first = next(i for i in attached if i['original_filename'] == 'IMG-20261112-WA0001.jpg')
        self.assertEqual(first['shared_at_local'], datetime(2026, 11, 12, 15, 55))
        self.assertEqual(first['caption'], 'Mehndi night\nsecond caption line')
        self.assertEqual([i['sender'] for i in items if i['kind'] == 'omitted'], ['Uma Ravi'])

    def test_import_with_provenance_dedup_and_report(self):
        s = self.run_import(aliases={'+1 (904) 900-9986': 'Cousin Anu'})
        recs = self.rows()
        self.assertEqual(len(recs), 4)  # 2 Vijaya photos, Shanthi video, aliased photo
        self.assertEqual(s['duplicates'], 1)
        self.assertEqual(len(list(self.dest.iterdir())), 3)  # identical bytes stored once
        self.assertEqual(recs[0]['local_path'], recs[1]['local_path'])
        vid = next(r for r in recs if r['media_type'] == 'video')
        self.assertEqual((vid['sender'], vid['shared_at_local']), ('Shanthi', '2026-11-12T16:00'))
        self.assertTrue((self.root / vid['local_path']).read_bytes() == b'video bytes')
        anu = recs[-1]
        self.assertEqual((anu['sender'], anu['sender_raw']), ('Cousin Anu', '+1 (904) 900-9986'))
        self.assertEqual([m['original_filename'] for m in s['missing']], ['IMG-20261112-WA0004.jpg'])
        self.assertEqual(dict(s['omitted']), {'Uma Ravi': 1})
        self.assertFalse(any(r['original_filename'].startswith('STK') for r in recs))

    def test_rerun_imports_nothing_new(self):
        self.run_import()
        s = self.run_import()
        self.assertEqual((sum(s['imported'].values()), s['already']), (0, 4))
        self.assertEqual(len(self.rows()), 4)

    def test_sender_filter_and_dry_run(self):
        s = self.run_import(sender='Shanthi', dry_run=True)
        self.assertEqual(dict(s['imported']), {('Shanthi', 'video'): 1})
        self.assertFalse(self.manifest.exists())
        self.assertEqual(list(self.dest.iterdir()), [])

    def test_day_month_order_detected_from_file_names(self):
        chat = '13/11/26, 09:06 - Ravi: IMG-20261113-WA0005.jpg (file attached)\n' \
               '05/11/26, 10:00 - Ravi: IMG-20261105-WA0001.jpg (file attached)'
        msgs = parse_android(chat)
        self.assertEqual(detect_date_order(msgs), 'dmy')
        self.assertEqual(attachments(msgs, 'dmy')[1]['shared_at_local'], datetime(2026, 11, 5, 10, 0))

    def test_documents_keep_original_names_and_can_be_imported_later(self):
        from media_archive.whatsapp_import import media_type
        self.assertEqual([media_type(n) for n in ('Order of Ceremony.pdf', 'Guest list.xlsx', 'Ravi.vcf', 'x.zip')],
                         ['document', 'document', 'contact', 'other'])
        chat = CHAT + '\n11/14/26, 10:00 AM - Lavanya Sanjeev: Order of Ceremony - Final.pdf (file attached)'
        self.zip = make_zip(self.zip, chat=chat, files={**FILES, 'Order of Ceremony - Final.pdf': b'%PDF-1.7'})
        self.run_import()
        s = self.run_import(types=('photo', 'video', 'document'))  # second pass adds only the PDF
        self.assertEqual(sum(s['imported'].values()), 1)
        pdf = self.rows()[-1]
        self.assertEqual((pdf['media_type'], pdf['original_filename']), ('document', 'Order of Ceremony - Final.pdf'))
        self.assertTrue(pdf['local_path'].endswith('.pdf'))

    def test_web_export_parse(self):
        rows = parse_web(WEB)
        self.assertEqual(len(rows), 7)
        self.assertEqual(rows[2], ('Shanthi', datetime(2026, 11, 12, 16, 0), 'video'))

    def test_cli_with_check_against_web_export(self):
        cfg = self.root / 'cfg.json'
        cfg.write_text(json.dumps({'archive_root': str(self.root), 'groups': [
            {'name': 'Test Group', 'directory': 'G', 'since': '2026-11-11'}]}))
        web = self.root / 'web.txt'; web.write_text(WEB, encoding='utf-8')
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            main([str(self.zip), '--config', str(cfg), '--check', str(web)])
        text = out.getvalue()
        self.assertIn('Imported: 4', text)
        self.assertIn('absent from the ZIP', text)
        self.assertIn('web lists 7 photos/videos, phone export has 6', text)
        self.assertIn('2026-11-14 08:00  KRISH  x1', text)

    def test_chat_names_and_settings(self):
        self.assertEqual(chat_name('/x/WhatsApp Chat with Anjali\u2764\ufe0f Samradh.zip'), 'Anjali\u2764\ufe0f Samradh')
        self.assertEqual(chat_name('WhatsApp Chat with Family Friends!!! (1).zip'), 'Family Friends!!!')
        cfg = {'since': '2026-09-01', 'aliases': {'+1': 'A', '+2': 'B'},
               'groups': [{'name': 'G', 'since': '2026-11-11', 'aliases': {'+2': 'Bee'}}]}
        self.assertEqual(chat_settings(cfg, 'G'), (date(2026, 11, 11), {'+1': 'A', '+2': 'Bee'}))
        self.assertEqual(chat_settings(cfg, 'Other'), (date(2026, 9, 1), {'+1': 'A', '+2': 'B'}))
        self.assertEqual(chat_settings({}, 'Other'), (None, {}))

    def test_several_chats_share_one_store_and_manifest(self):
        other = make_zip(self.root / 'WhatsApp Chat with Family Group.zip', chat='\n'.join([
            '11/12/26, 5:00 PM - Anjali: IMG-20261112-WA0100.jpg (file attached)',  # same bytes, forwarded
            '11/12/26, 5:01 PM - Anjali: IMG-20261112-WA0101.jpg (file attached)']),
            files={'IMG-20261112-WA0100.jpg': b'same bytes', 'IMG-20261112-WA0101.jpg': b'new'})
        cfg = self.root / 'cfg.json'
        cfg.write_text(json.dumps({'archive_root': str(self.root), 'since': '2026-11-11'}))
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            main([str(self.zip), str(other), '--config', str(cfg)])
        store = self.root / 'WhatsApp' / 'Media'
        recs = [json.loads(l) for l in (self.root / 'Metadata' / 'whatsapp-import' / 'manifest.jsonl')
                .read_text(encoding='utf-8').splitlines()]
        self.assertEqual(len(recs), 6)
        self.assertEqual({r['chat'] for r in recs}, {'Test Group', 'Family Group'})
        self.assertEqual(len(list(store.iterdir())), 4)  # 'same bytes' stored once across both chats
        shared = [r for r in recs if (self.root / r['local_path']).read_bytes() == b'same bytes']
        self.assertEqual({(r['chat'], r['sender']) for r in shared},
                         {('Test Group', 'Vijaya Mahesh'), ('Family Group', 'Anjali')})
        self.assertIn('All chats: imported 6', out.getvalue())
        with contextlib.redirect_stdout(io.StringIO()):
            main([str(self.zip), str(other), '--config', str(cfg)])  # re-run adds nothing
        self.assertEqual(len((self.root / 'Metadata' / 'whatsapp-import' / 'manifest.jsonl')
                             .read_text(encoding='utf-8').splitlines()), 6)


if __name__ == '__main__':
    unittest.main()
