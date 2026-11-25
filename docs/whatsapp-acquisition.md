# WhatsApp photo acquisition — experimental

Exit the discovery command first (both commands use the same local browser profile).
Run `python -m media_archive.whatsapp_acquire --group 1` from the activated project environment. Optional `--sender` limits exact sender. Open the configured group (or try `a`), use WhatsApp date search or scroll, then press Enter to scan. Only dates on or after the configured start date are eligible; dates are interpreted in the observed US format (12- or 24-hour clock) and browser-local timezone (no offset inferred). Choose a numbered photo, verify the viewer, and confirm `y`. If instructed, click the native Download button yourself. Close the viewer before rescanning.

Files are saved under the configured archive root / WhatsApp / group directory, with SHA-256 filenames. The original suggested filename, sender, timestamp, message ID and attachment index are appended to Metadata/whatsapp-acquisition/<directory>.jsonl. Existing identical bytes with the same extension are reused in the group. Resume uses message ID and visible attachment index; album layout changes may affect indexes, so this is provisional. The native download retrieves WhatsApp's available version, not necessarily the camera original. No thumbnails are deliberately downloaded. Do not commit captures, manifest or browser profile.

This increment does not traverse history automatically, handle albums hidden behind additional-photo overlays, download videos/documents, or guarantee identification of all photo controls. Quoted-message containers are conservatively skipped. Live UI selectors require validation. Run only one acquisition process at a time.

## Browser lifecycle

Both commands share `media_archive/browser.py`:

- **Ctrl+C** stops only the Python process; the tool then closes Chromium gracefully (exit code 130) so the WhatsApp login in the profile is saved cleanly. Previously Ctrl+C killed Chromium as well, and the final `context.close()` raised `TargetClosedError`, hiding the interrupt — the likely cause of the first live-test failure.
- **Closing the Chromium window** ends the run with a message naming the step in progress (exit code 2) instead of a traceback. Re-run to continue; recorded downloads are kept.
- **Closing only the WhatsApp tab** reopens WhatsApp in a new tab at the next command; reopen the group before scanning.
- **Profile already in use** (discovery still running, or a leftover Chromium window) is reported plainly. Quit the other command first.

## Tests

`python -m unittest discover -s tests` runs the unit tests and an end-to-end test of the acquisition loop against a local mock page (`tests/fixtures/whatsapp_mock.html`; synthetic data, no network). The end-to-end test downloads through Chromium and checks sender metadata, resume, SHA-256 deduplication and the lifecycle cases above. It needs `python -m playwright install chromium`, or set `MEDIA_ARCHIVE_TEST_CHROME` to a Chrome binary. The mock validates this tool's logic, not WhatsApp's live selectors.
