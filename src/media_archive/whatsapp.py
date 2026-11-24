"""Inspect rendered WhatsApp Web messages in a dedicated local browser."""
import argparse
import json
import re
from datetime import datetime, timezone
from pathlib import Path

SNAPSHOT = """() => Array.from(document.querySelectorAll('#main [data-pre-plain-text]')).map(e => {
  const root = e.closest('[data-id]') || e;
  return {header: e.getAttribute('data-pre-plain-text'),
          message_id: root.getAttribute('data-id'),
          text: e.innerText,
          images: root.querySelectorAll('img').length,
          videos: root.querySelectorAll('video').length,
          labels: Array.from(root.querySelectorAll('[aria-label], [title]')).map(x => x.getAttribute('aria-label') || x.getAttribute('title')).filter(Boolean).slice(0,30)};
})"""

def parse_header(header):
    match = re.match(r'^\[([^\]]+)\]\s*(.*?):\s*$', header or '')
    return {'timestamp_raw': match[1], 'sender': match[2]} if match else {'timestamp_raw': None, 'sender': None}

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', default='config/whatsapp.local.json')
    parser.add_argument('--group', type=int, default=1, help='One-based group index')
    args = parser.parse_args()
    config = json.loads(Path(args.config).read_text(encoding='utf-8'))
    if not 1 <= args.group <= len(config['groups']): parser.error('Invalid group index')
    group = config['groups'][args.group-1]
    root = Path(config['archive_root']).expanduser()
    if not root.is_dir(): parser.error(f'Archive root not found: {root}')
    dest = root / 'Metadata' / 'whatsapp-discovery' / group['directory']
    dest.mkdir(parents=True, exist_ok=True)
    profile = Path.home() / '.local/share/media-archive/whatsapp-profile'
    profile.mkdir(parents=True, exist_ok=True, mode=0o700)
    from playwright.sync_api import sync_playwright
    with sync_playwright() as p:
        context = p.chromium.launch_persistent_context(str(profile), headless=False)
        try:
            page = context.pages[0] if context.pages else context.new_page()
            page.goto('https://web.whatsapp.com/', wait_until='domcontentloaded')
            print(f"Open group: {group['name']}\nTarget date: {group['since']}\nValidation sender: {group.get('validation_sender', '(any)')}")
            print('Scan QR if requested. Open the exact group, navigate to target date, and verify the visible messages.')
            print('Enter = capture loaded messages; q = quit. Scroll between captures to inspect more messages.')
            while True:
                command = input('Capture [Enter/q]: ').strip().lower()
                if command == 'q': break
                if command: continue
                header_info = page.evaluate("""() => {
                  const h = document.querySelector('#main header');
                  if (!h) return {present: false, candidates: []};
                  const candidates = new Set();
                  for (const e of [h, ...h.querySelectorAll('*')]) {
                    for (const a of ['title', 'aria-label']) {
                      const v = e.getAttribute(a);
                      if (v) candidates.add(v.trim());
                    }
                    if (e.innerText && e.getClientRects().length) {
                      for (const line of e.innerText.split('\\n')) {
                        if (line.trim()) candidates.add(line.trim());
                      }
                    }
                  }
                  return {present: true, candidates: [...candidates]};
                }""")
                headers = header_info['candidates']
                if group['name'] not in headers:
                    print('Could not verify exact group header. No data saved.')
                    print(f"Chat header present: {header_info['present']}")
                    print('Header candidates (private; inspect locally):')
                    for candidate in headers[:40]: print(repr(candidate))
                    print('Open the exact group in the Chromium window launched by this tool and retry.')
                    continue
                print(f"Verified group: {group['name']}")
                rows = page.evaluate(SNAPSHOT)
                for row in rows: row.update(parse_header(row['header']))
                stamp = datetime.now(timezone.utc)
                path = dest / (stamp.strftime('%Y%m%dT%H%M%S%fZ') + '.json')
                path.write_text(json.dumps({'group':group['name'],'target_since':group['since'],'captured_at':stamp.isoformat(),'messages':rows},ensure_ascii=False,indent=2),encoding='utf-8')
                print(f'{len(rows)} loaded message records saved to {path}')
                if not rows: print('No matching message elements found; the WhatsApp UI may have changed.')
        finally:
            context.close()

if __name__ == '__main__': main()
