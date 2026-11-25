"""Shared lifecycle for the dedicated WhatsApp Web browser profile.

Handles the ways the first live test could end in ``TargetClosedError``:
the user pressing Ctrl+C (which also terminates Chromium, so the final
``context.close()`` failed and masked the interrupt), the user closing the
browser window or tab, the page crashing, or the profile already being in use
by another media-archive process.
"""
import signal
import sys
from contextlib import contextmanager
from pathlib import Path

PROFILE = Path.home() / '.local/share/media-archive/whatsapp-profile'
WHATSAPP_URL = 'https://web.whatsapp.com/'


class BrowserClosed(RuntimeError):
    """The browser (or every page in it) is gone; the run cannot continue."""


def is_target_closed(exc):
    """True for Playwright's TargetClosedError, without importing private modules."""
    return type(exc).__name__ == 'TargetClosedError' or 'has been closed' in str(exc)


class Session:
    """Wraps a persistent context and keeps a usable page available."""

    def __init__(self, context, url=WHATSAPP_URL):
        self.context = context
        self.url = url
        self.closed = False
        self.closing = False
        self.stage = 'starting the browser'  # Reported if the browser closes unexpectedly.
        self._page = None
        context.on('close', self._on_context_close)

    def _on_context_close(self, *_):
        self.closed = True

    def _watch(self, page):
        page.on('crash', lambda *_: print('\nThe WhatsApp tab crashed. It will be reloaded at the next command.', flush=True))
        page.on('close', lambda *_: None if self.closed or self.closing else print('\nThe WhatsApp tab was closed.', flush=True))
        return page

    def open(self):
        pages = [p for p in self.context.pages if not p.is_closed()]
        self._page = self._watch(pages[0] if pages else self.context.new_page())
        self._page.goto(self.url, wait_until='domcontentloaded')
        return self._page

    @property
    def page(self):
        """Current page; reopens WhatsApp in a new tab if the user closed the tab."""
        if self.closed:
            raise BrowserClosed('The browser window was closed.')
        if self._page is None or self._page.is_closed():
            try:
                print('Reopening WhatsApp in a new tab; reopen the group before scanning.', flush=True)
                return self.open()
            except Exception as exc:
                if is_target_closed(exc):
                    self.closed = True
                    raise BrowserClosed('The browser window was closed.') from exc
                raise
        return self._page

    def close(self):
        if self.closed:
            return
        self.closing = True
        try:
            self.context.close()
        except Exception as exc:  # Already gone (e.g. after Ctrl+C) is not an error.
            if not is_target_closed(exc):
                raise
        finally:
            self.closed = True


def launch(pw, profile=PROFILE, url=WHATSAPP_URL, **options):
    profile = Path(profile)
    profile.mkdir(parents=True, exist_ok=True, mode=0o700)
    try:
        context = pw.chromium.launch_persistent_context(str(profile), **options)
    except Exception as exc:
        text = str(exc)
        if 'ProcessSingleton' in text or 'already in use' in text:
            sys.exit('The WhatsApp browser profile is already in use. Quit the other media-archive '
                     'command (discovery or acquisition) and close its Chromium window, then retry.')
        if is_target_closed(exc):
            sys.exit('Chromium exited during start-up. If another media-archive browser is open, close it '
                     'and retry. Otherwise run "python -m playwright install chromium" and retry.')
        raise
    session = Session(context, url)
    session.open()
    return session


def _start_playwright():
    """Start the Playwright driver so that a terminal Ctrl+C reaches only this Python process.

    Previously Ctrl+C also killed the driver and Chromium, so the cleanup ``context.close()``
    raised TargetClosedError (masking the interrupt) and the profile was not shut down cleanly.
    The driver inherits an ignored SIGINT; Chromium runs in its own process group and
    ``handle_sigint=False`` stops the driver from killing it. Python then closes the browser.
    """
    from playwright.sync_api import sync_playwright
    manager = sync_playwright()
    try:
        previous = signal.signal(signal.SIGINT, signal.SIG_IGN)
    except ValueError:  # Not in the main thread (e.g. some test runners): start normally.
        return manager.start()
    try:
        return manager.start()
    finally:
        signal.signal(signal.SIGINT, previous)


@contextmanager
def whatsapp_session(url=WHATSAPP_URL, profile=PROFILE, **options):
    """Yield a Session; always shuts down cleanly and turns Ctrl+C / closed window into clear exits."""
    options.setdefault('headless', False)
    options.setdefault('handle_sigint', False)
    session = None
    pw = _start_playwright()
    try:
        session = launch(pw, profile, url, **options)
        yield session
    except KeyboardInterrupt:
        print('\nInterrupted. Closing the browser; progress already recorded in the manifest is kept.')
        _quiet_close(session)
        sys.exit(130)
    except BrowserClosed:
        print(f"\nThe browser window was closed while {_stage(session)}. "
              'Progress already recorded in the manifest is kept; re-run to continue.')
        sys.exit(2)
    except Exception as exc:
        if is_target_closed(exc):
            print(f"\nThe browser closed unexpectedly while {_stage(session)}. "
                  'Progress already recorded in the manifest is kept; re-run to continue.')
            sys.exit(2)
        raise
    finally:
        _quiet_close(session)
        try:
            pw.stop()
        except Exception:
            pass


def _quiet_close(session):
    if session is None:
        return
    try:
        session.close()
    except Exception as exc:
        print(f'Browser did not close cleanly: {exc}')


def _stage(session):
    return session.stage if session else 'starting the browser'
