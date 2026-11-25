# Reviewing imported media

The archive (`<archive_root>/WhatsApp/Media`, SHA-256 names) is never edited by hand. Review happens in a separate folder of readable copies:

```bash
python -m media_archive.review build --dry-run   # what would be copied
python -m media_archive.review build             # copy into <archive_root>/Review
```

- Photos and videos: `Review/<YYYY-MM-DD>/<YYYY-MM-DD_HHMM>_<Sender>_<Chat>.<ext>`, using the first time the file was shared (the original poster). File dates are set to that time so photo apps sort correctly.
- Documents: `Review/Documents/<YYYY-MM-DD>_<original name>.<ext>`.
- A photo forwarded to several chats appears once; every chat and sender is kept in `Metadata/review/index.json`.

Then, in Explorer or a photo app, **delete what you don't want**. Renaming files or moving them into your own subfolders (e.g. `Review/Favourites/`) is fine.

```bash
python -m media_archive.review status    # kept / deleted-not-applied / rejected
python -m media_archive.review apply     # record deletions as rejected
```

`build` can be re-run after new imports: it adds only new files and never re-adds rejected ones. `restore 2026-11-11` copies the deleted files of one day folder back (whether or not `apply` has run); `restore` with a file name or glob (`'*Shanthi*'`) works too, and plain `restore` brings back every deleted file. Rejections are stored in `Metadata/review/rejected.json`.

Review copies duplicate the space used by the archive; the folder can be deleted and rebuilt (rejections are remembered).

## File dates on a manually mounted drive (WSL)

Setting file dates needs file ownership. A drive mounted with plain `sudo mount -t drvfs D: /mnt/d` shows every file as owned by root, so dates cannot be set (the build continues and prints a note). Mount with your user id instead, then run `python -m media_archive.review dates`:

```bash
sudo umount -l /mnt/d 2>/dev/null; sudo mkdir -p /mnt/d
sudo mount -t drvfs D: /mnt/d -o metadata,uid=$(id -u),gid=$(id -g)
```

An interrupted build (Ctrl+C, unplugged drive) can simply be re-run: files already copied are recognised, not duplicated.
