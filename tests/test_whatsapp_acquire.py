import tempfile
import unittest
from datetime import date
from pathlib import Path
from media_archive.whatsapp_acquire import eligible, save_download, existing

class FakeDownload:
    suggested_filename = 'photo.jpg'
    def save_as(self,path): Path(path).write_bytes(b'test bytes')

class TestAcquire(unittest.TestCase):
    def test_dates_and_sender(self):
        rows=[dict(header=f'[5:49 AM, 11/{d}/2026] Example: ',message_id=str(d),index=0) for d in (9,11,12)]
        self.assertEqual(len(eligible(rows,date(2026,11,11),'Example')),2)
        self.assertEqual(eligible(rows,date(2026,11,11),'Other'),[])
    def test_save_and_resume(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp); dest=root/'photos'; dest.mkdir(); manifest=root/'manifest.jsonl'
            row={'message_id':'test','index':0}
            first=save_download(FakeDownload(),row,'Example',root,dest,manifest)
            second=save_download(FakeDownload(),dict(row,index=1),'Example',root,dest,manifest)
            self.assertEqual(first,second)
            self.assertEqual(existing(manifest,root),{('test',0),('test',1)})
