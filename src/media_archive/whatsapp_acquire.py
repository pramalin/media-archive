"""Acquire visible photo attachments using the native WhatsApp download UI."""
import argparse
import hashlib
import json
import re
import uuid
from datetime import datetime, timezone, date
from pathlib import Path
from .whatsapp import parse_header

PHOTO_SELECTOR = '[aria-label="Open picture"], [title="Open picture"]'
HEADER_CHECK = """name => { const h = document.querySelector('#main header');
return !!h && [h,...h.querySelectorAll('*')].some(e =>
 e.getAttribute('title') === name || e.getAttribute('aria-label') === name ||
 (e.getClientRects().length && (e.innerText || '').split('\\n').some(t => t.trim() === name))); }"""

# Only explicit picture-opening controls, not every img or reaction.
SCAN = """selector => { const out = []; const seen = new Set();
for (const e of document.querySelectorAll('#main [data-pre-plain-text]')) {
 const root = e.closest('[data-id]'); if (!root || seen.has(root)) continue;
 seen.add(root); const id = root.getAttribute('data-id');
 const controls = [...root.querySelectorAll(selector)].filter(x => x.getClientRects().length &&
 !x.closest('[aria-label="Quoted message"], [title="Quoted message"]') &&
 !x.parentElement.closest(selector));
 // Refuse ambiguous quoted messages for this first increment.
 if (root.querySelector('[aria-label="Quoted message"], [title="Quoted message"]')) continue;
 for (let i=0;i<controls.length;i++) out.push({message_id:id, index:i,
 header:e.getAttribute('data-pre-plain-text'), text:e.innerText});
} return out; }"""

def parse_time(raw):
    return datetime.strptime(raw, '%I:%M %p, %m/%d/%Y')

def eligible(rows, since, sender=None):
    result = []
    for row in rows:
        info = parse_header(row['header'])
        try:
            stamp = parse_time(info['timestamp_raw'])
        except (TypeError, ValueError):
            print('Skipping attachment with unrecognized timestamp.')
            continue
        if row.get('message_id') and stamp.date() >= since and (not sender or info['sender'] == sender):
            result.append({**row, **info, 'shared_at_local':stamp.isoformat()})
    return result

def sha256(path):
    h = hashlib.sha256()
    with path.open('rb') as f:
        for chunk in iter(lambda:f.read(1024*1024), b''):
            h.update(chunk)
    return h.hexdigest()

def key(row):
    return (row['message_id'], row['index'])

def existing(manifest, root):
    done = set()
    if manifest.exists():
        for line in manifest.read_text(encoding='utf-8').splitlines():
            row = json.loads(line)
            if (root / row['local_path']).is_file(): done.add(key(row))
    return done

def save_download(download, row, group, root, dest, manifest):
    name = re.split(r'[/\\\\]', download.suggested_filename)[-1]
    suffix = Path(name).suffix.lower()
    if suffix not in {'.jpg','.jpeg','.png','.webp','.gif','.heic','.heif','.bmp','.avif'}:
        raise ValueError(f'Unexpected photo file extension: {suffix!r}')
    tmp = dest / (uuid.uuid4().hex + '.part')
    try:
        download.save_as(str(tmp))
        digest = sha256(tmp)
        size = tmp.stat().st_size
        if not size: raise ValueError('Empty download')
        final = dest / (digest + suffix)
        if final.exists():
            if sha256(final) != digest: raise ValueError('Existing file hash mismatch')
        else: tmp.replace(final)
        record = {**row, 'group':group, 'original_filename':name,
                  'local_path':final.relative_to(root).as_posix(), 'sha256':digest,
                  'size_bytes':size, 'downloaded_at':datetime.now(timezone.utc).isoformat()}
        with manifest.open('a',encoding='utf-8') as f:
            f.write(json.dumps(record,ensure_ascii=False)+'\n')
        return final
    finally:
        tmp.unlink(missing_ok=True)

def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--config',default='config/whatsapp.local.json')
    p.add_argument('--group',type=int,default=1)
    p.add_argument('--sender',help='Optional exact sender filter')
    args = p.parse_args()
    cfg = json.loads(Path(args.config).read_text(encoding='utf-8'))
    if not 1 <= args.group <= len(cfg['groups']): p.error('Invalid group index')
    group = cfg['groups'][args.group-1]
    root = Path(cfg['archive_root']).expanduser()
    if not root.is_dir(): p.error(f'Archive root not found: {root}')
    since = date.fromisoformat(group['since'])
    dest = root / 'WhatsApp' / group['directory']; dest.mkdir(parents=True,exist_ok=True)
    manifest = root / 'Metadata' / 'whatsapp-acquisition' / (group['directory']+'.jsonl')
    manifest.parent.mkdir(parents=True,exist_ok=True)
    done = existing(manifest,root)
    from playwright.sync_api import sync_playwright, TimeoutError as PWTimeout
    profile = Path.home()/'.local/share/media-archive/whatsapp-profile'
    with sync_playwright() as pw:
        context = pw.chromium.launch_persistent_context(str(profile),headless=False,accept_downloads=True)
        try:
            page = context.pages[0] if context.pages else context.new_page()
            page.goto('https://web.whatsapp.com/',wait_until='domcontentloaded')
            print(f"Open {group['name']} and navigate to {since} or later.")
            while True:
                cmd = input('Enter = scan visible photos, a = attempt group selection, q = quit: ').strip().lower()
                if cmd == 'q': break
                if cmd == 'a':
                    matches = page.locator('#pane-side').get_by_text(group['name'],exact=True)
                    if matches.count() == 1: matches.click(timeout=5000)
                    else: print('Select the group manually.')
                    continue
                if cmd: continue
                if not page.evaluate(HEADER_CHECK,group['name']):
                    print('Group not verified. No download.'); continue
                rows = [r for r in eligible(page.evaluate(SCAN,PHOTO_SELECTOR),since,args.sender) if key(r) not in done]
                print(f'{len(rows)} eligible visible photo controls not yet recorded.')
                for i,row in enumerate(rows,1): print(f"{i}: {row['sender']} | {row['timestamp_raw']} | photo {row['index']+1}")
                if not rows: continue
                choice = input('Photo number to open (Enter = rescan): ').strip()
                if not choice.isdigit() or not 1 <= int(choice) <= len(rows): continue
                row = rows[int(choice)-1]
                if not page.evaluate(HEADER_CHECK,group['name']): continue
                # Re-scan to avoid stale message or changed control ordering.
                if row not in eligible(page.evaluate(SCAN,PHOTO_SELECTOR),since,args.sender):
                    print('Message changed; rescan.'); continue
                escaped = json.dumps(row['message_id'],ensure_ascii=False)
                controls = page.locator(f'#main [data-id={escaped}]').locator(", ".join(s+":visible" for s in PHOTO_SELECTOR.split(", ")))
                # Ensure locator matches the exact same filtered control set used in scan.
                handles = controls.element_handles()
                handles = [h for h in handles if h.evaluate('(x,s) => !x.closest(\'[aria-label="Quoted message"], [title="Quoted message"]\') && !x.parentElement.closest(s)',PHOTO_SELECTOR)]
                try:
                    handles[row['index']].click(timeout=5000)
                except (IndexError,PWTimeout):
                    print('Could not open photo; rescan.'); continue
                print('Check the opened photo belongs to the selected message. Do not navigate to another photo.')
                if input('Correct photo? [y/N]: ').strip().lower() != 'y':
                    print('Close the viewer manually before rescanning.'); continue
                try:
                    # Only use a unique visible Download control outside the conversation.
                    buttons = page.locator('[aria-label="Download"]:visible, [title="Download"]:visible')
                    candidates = [b for b in buttons.element_handles() if b.evaluate('e => !e.closest("#main")')]
                    if len(candidates)==1:
                        with page.expect_download(timeout=60000) as event: candidates[0].click(timeout=5000)
                    else:
                        print('Click the Download icon in the opened viewer now (60 seconds).',flush=True)
                        with page.expect_download(timeout=60000) as event: pass
                    final = save_download(event.value,row,group['name'],root,dest,manifest)
                    done.add(key(row)); print(f'Saved {final}\nManifest: {manifest}')
                except (PWTimeout,ValueError,OSError) as exc:
                    print(f'Download not recorded: {exc}')
                print('Close the media viewer manually before the next scan.')
        finally: context.close()

if __name__=='__main__': main()
