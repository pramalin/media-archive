# WhatsApp discovery (increment 01)

Run in WSL with WSLg graphical support, using a dedicated Chromium profile. This does not attach to an existing Windows Chrome session. Install Python venv support if needed.

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e '.[whatsapp]'
python -m playwright install chromium
python -m media_archive.whatsapp --group 1
```

If Chromium reports missing system libraries, run `python -m playwright install-deps chromium` (requires sudo). If WSL has no graphical support, stop and use a Windows Python environment with a Windows archive path instead.

Copy config/whatsapp.example.json to config/whatsapp.local.json and customize if no local config is supplied. Group indices are one-based. Scan the QR code using Linked devices on your phone. Select the exact configured group and navigate to the target date using the chat search/calendar if available, or scroll. Press Enter in the terminal to capture; scroll and repeat as needed; q closes the browser. The session persists outside the repository under ~/.local/share/media-archive/whatsapp-profile. Do not share this directory.

JSON snapshots are saved to the SSD under Metadata/whatsapp-discovery. They contain private message text and raw sender/time labels. The date is a navigation hint, not an enforced filter. Only currently rendered messages are captured; older messages may be unavailable. Media counts/labels are diagnostic hints, not reliable attachment classifications. No media are downloaded, no messages are sent, and no completeness or deduplication is claimed. WhatsApp DOM selectors are unofficial and may change. Verify group and sender manually. Do not commit snapshots or local configuration. Use only your own authorized chats and comply with WhatsApp terms.

If a capture fails due to group header or empty messages, report the error without posting private chat content. Run unit tests with `python -m unittest discover -s tests` after editable installation.
