from types import SimpleNamespace
from unittest import mock

import scheduler


def test_waits_without_passphrase_then_unlocks_once_set(monkeypatch):
    monkeypatch.delenv("STREAMSAVER_PASSPHRASE", raising=False)
    sleeps = []

    def fake_sleep(seconds):
        sleeps.append(seconds)
        monkeypatch.setenv("STREAMSAVER_PASSPHRASE", "sample-pass")

    with mock.patch.object(scheduler.sys.stdin, "isatty", return_value=False), \
         mock.patch.object(scheduler.time, "sleep", side_effect=fake_sleep), \
         mock.patch.object(scheduler, "load_credentials", return_value={"k": "v"}) as load, \
         mock.patch.object(scheduler, "_notify_discord") as notify:
        passphrase, creds = scheduler._wait_for_credentials(SimpleNamespace(passphrase=None))
    assert (passphrase, creds) == ("sample-pass", {"k": "v"})
    assert sleeps == [scheduler.CREDENTIAL_RETRY_SECONDS]
    load.assert_called_once_with("sample-pass")
    notify.assert_not_called()  # no passphrase yet is normal first-run state


def test_bad_credentials_notify_once_and_keep_retrying(monkeypatch):
    monkeypatch.setenv("STREAMSAVER_PASSPHRASE", "sample-pass")
    attempts = iter([RuntimeError("bad"), RuntimeError("bad"), {"k": "v"}])

    def fake_load(_):
        result = next(attempts)
        if isinstance(result, Exception):
            raise result
        return result

    with mock.patch.object(scheduler.time, "sleep"), \
         mock.patch.object(scheduler, "load_credentials", side_effect=fake_load), \
         mock.patch.object(scheduler, "_notify_discord") as notify:
        assert scheduler._wait_for_credentials(SimpleNamespace(passphrase=None))[1] == {"k": "v"}
    assert notify.call_count == 1
