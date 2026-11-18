"""Suite-wide guards."""
from __future__ import annotations

import time

import pytest


@pytest.fixture(autouse=True)
def _reload_timezone_after_each_test():
    """Re-read TZ after every test, so a zone one test set never leaks into the next.

    monkeypatch restores `os.environ["TZ"]` on teardown but cannot call
    `time.tzset()`, so libc would keep the test's zone while the environment
    says otherwise. This runs after monkeypatch's own teardown (it was set up
    first), so tzset sees the restored value.
    """
    yield
    time.tzset()
