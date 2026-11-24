# WhatsApp photo acquisition — experimental

Exit the discovery command first (both commands use the same local browser profile).
Run `python -m media_archive.whatsapp_acquire --group 1` from the activated project environment. Optional `--sender` limits exact sender. Open the configured group (or try `a`), use WhatsApp date search or scroll, then press Enter to scan. Only dates on or after the configured start date are eligible; dates are interpreted in the observed US format and browser-local timezone (no offset inferred). Choose a numbered photo, verify the viewer, and confirm `y`. If instructed, click the native Download button yourself. Close the viewer before rescanning.

Files are saved under the configured archive root / WhatsApp / group directory, with SHA-256 filenames. The original suggested filename, sender, timestamp, message ID and attachment index are appended to Metadata/whatsapp-acquisition/<directory>.jsonl. Existing identical bytes with the same extension are reused in the group. Resume uses message ID and visible attachment index; album layout changes may affect indexes, so this is provisional. The native download retrieves WhatsApp's available version, not necessarily the camera original. No thumbnails are deliberately downloaded. Do not commit captures, manifest or browser profile.

This increment does not traverse history automatically, handle albums hidden behind additional-photo overlays, download videos/documents, or guarantee identification of all photo controls. Quoted-message containers are conservatively skipped. Live UI selectors require validation. Run only one acquisition process at a time.

Tests: `python -m unittest discover -s tests -p test_whatsapp_acquire.py`.
