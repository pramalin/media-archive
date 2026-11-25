# Media Archive

A local-first media archival, organization and presentation toolkit.

## Objectives

- Consolidate photographs and videos from multiple sources.
- Preserve original media and provenance (sender, group and sharing time).
- Extract metadata and identify duplicate media.
- Provide a searchable gallery and curated collections.
- Generate automated 3D presentations using Blender.

## Setup (WSL)

Copy `config/example.json` to `config/local.json` and set the archive root to your external storage (for example, `/mnt/d/Wedding-2026`).

```bash
cp -n config/example.json config/local.json
python3 -m venv .venv
source .venv/bin/activate
pip install -e .
```

Source code belongs in Git; private media, chat exports and catalogs belong on external storage. Do not commit personal data.

## Initial milestone

Acquire WhatsApp media and preserve sender metadata where available. Recommended: import an Android "Export chat" ZIP — see `docs/whatsapp-import.md`. Photos from your own phone: `docs/phone-originals.md`; WhatsApp media saved on the phone: `docs/whatsapp-folder.md`. Then review and prune with readable copies — see `docs/review.md`. Browser-based tools (fallback): `docs/whatsapp-discovery.md`, `docs/whatsapp-acquisition.md`.
