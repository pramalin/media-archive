"""End-to-end acquisition tests against a local mock of WhatsApp Web (no network, no real chats).

Needs Playwright's Chromium (``python -m playwright install chromium``) or a Chrome binary given
in MEDIA_ARCHIVE_TEST_CHROME. Skipped when neither is available.
"""
import contextlib
import io
import json
import os
import tempfile
import unittest
from datetime import date
from pathlib import Path

from media_archive.whatsapp_acquire import run

MOCK = (Path(__file__).parent / 'fixtures' / 'whatsapp_mock.html').resolve().as_uri()
GROUP = {'name': 'Test Wedding Group', 'directory': 'Test-Group', 'since': '2026-11-01'}

try:
    from media_archive.browser import whatsapp_session
    import playwright.sync_api  # noqa: F401
except ImportError:  # pragma: no cover
    whatsapp_session = None


def chrome_options():
    path = os.environ.get('MEDIA_ARCHIVE_TEST_CHROME')
    return {'executable_path': path} if path else {}


class Scripted:
    """Feeds answers to the prompts; callables run an action (e.g. closing the browser) instead."""
    def __init__(self, session, answers):
        self.session, self.answers, self.prompts = session, list(answers), []

    def __call__(self, prompt):
        self.prompts.append(prompt)
        answer = self.answers.pop(0)
        return answer(self.session) if callable(answer) else answer


@unittest.skipIf(whatsapp_session is None, 'playwright not installed')
class AcquireAgainstMock(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory(); self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.dest = self.root / 'WhatsApp' / GROUP['directory']; self.dest.mkdir(parents=True)
        self.manifest = self.root / 'Metadata' / 'acq.jsonl'; self.manifest.parent.mkdir()
        self.profile = self.root / 'profile'

    def acquire(self, answers, sender='Shanthi'):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            try:
                with whatsapp_session(url=MOCK, profile=self.profile, headless=True,
                                      accept_downloads=True, **chrome_options()) as session:
                    run(session, GROUP, date(2026, 11, 1), self.root, self.dest, self.manifest,
                        sender, ask=Scripted(session, answers))
            except Exception as exc:
                if 'Executable doesn' in str(exc) or 'executable' in str(exc).lower():
                    self.skipTest(f'No Chromium available: {exc}')
                raise
        return out.getvalue()

    def records(self):
        if not self.manifest.exists(): return []
        return [json.loads(l) for l in self.manifest.read_text(encoding='utf-8').splitlines()]

    def test_download_one_photo_with_sender_metadata_then_resume(self):
        out = self.acquire(['', '1', 'y', 'q'])
        self.assertIn('3 eligible visible photo controls', out)  # msg-1, msg-3 x2; old, Ravi, quoted excluded
        [rec] = self.records()
        self.assertEqual((rec['sender'], rec['message_id'], rec['index']), ('Shanthi', 'msg-1', 0))
        self.assertEqual(rec['original_filename'], 'IMG-20261112-WA0001.jpg')
        self.assertEqual(rec['shared_at_local'], '2026-11-12T17:49:00')
        saved = self.root / rec['local_path']
        self.assertEqual(saved.read_bytes(), b'photo bytes: first')
        self.assertEqual(saved.name, rec['sha256'] + '.jpg')
        out = self.acquire(['', '', 'q'])  # Resume: the recorded photo is no longer offered.
        self.assertIn('2 eligible visible photo controls', out)

    def test_identical_bytes_are_stored_once(self):
        self.acquire(['', '1', 'y', '', '1', 'y', 'q'])  # msg-1 then msg-3 photo 1 (same bytes, 24h clock)
        a, b = self.records()
        self.assertEqual(a['local_path'], b['local_path'])
        self.assertEqual(b['shared_at_local'], '2026-11-12T18:10:00')
        self.assertEqual(len(list(self.dest.iterdir())), 1)

    def test_rejected_photo_is_not_recorded(self):
        self.acquire(['', '1', 'n', 'q'])
        self.assertEqual(self.records(), [])

    def test_browser_window_closed_exits_cleanly(self):
        with self.assertRaises(SystemExit) as cm:
            self.acquire([lambda s: s.context.close() or ''])
        self.assertEqual(cm.exception.code, 2)

    def test_ctrl_c_with_browser_already_gone_exits_cleanly(self):
        def interrupt(session):
            session.context.close()  # Ctrl+C also terminates Chromium in the same process group.
            raise KeyboardInterrupt
        with self.assertRaises(SystemExit) as cm:
            self.acquire([interrupt])
        self.assertEqual(cm.exception.code, 130)

    def test_diagnose_lists_messages_without_caption_header(self):
        out = self.acquire(['d', 'q'])
        self.assertIn('6 rendered messages', out)
        self.assertIn("id=..msg-5 header=None large_images=1", out)
        self.assertIn('Open picture', out)

    def test_closed_tab_is_reopened(self):
        out = self.acquire([lambda s: s.page.close() or '', '', 'q'])
        self.assertIn('Reopening WhatsApp in a new tab', out)
        self.assertIn('3 eligible visible photo controls', out)


if __name__ == '__main__':
    unittest.main()
