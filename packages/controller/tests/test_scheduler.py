"""The release scheduler and the `scheduler` trigger backend (CTL-REL; PLAN §8.1 releases row, §15 item 51;
`$DRAFTS/06 §5.14` "Scheduler", §11.2 `test_scheduler.py`).

`tick` is driven with explicit minutes; release documents are written straight into the controller's DocStore and
`ctl.releases` is a recorder, so these tests cover the scheduler alone (firing itself is `test_releases.py`).
"""

from __future__ import annotations

import threading
from datetime import UTC, datetime, timedelta

import pytest

from support.ctl_rel_fakes import install_backends
from wynd.controller.errors import Invalid
from wynd.controller.releases.scheduler import Scheduler
from wynd.controller.releases.store import RELEASES
from wynd.controller.releases.triggers import SchedulerTriggers, open_trigger_backend

T0 = datetime(2026, 9, 22, 12, 0, tzinfo=UTC)


def minute(n: int) -> datetime:
    return T0 + timedelta(minutes=n)


class RecordingReleases:
    """Stands in for `ctl.releases`: records each fire with the release's `last_fire_key` as stored at that moment."""

    def __init__(self, ctl) -> None:
        self.ctl = ctl
        self.fired: list[tuple[str, str | None, str]] = []
        self.fail: Exception | None = None
        self.done = threading.Semaphore(0)

    def trigger(self, release_id, inputs, *, source):
        key = self.ctl.ctx.docs.get(RELEASES, release_id)["last_fire_key"]
        self.fired.append((release_id, key, source))
        self.done.release()
        if self.fail is not None:
            raise self.fail
        assert inputs is None


def put_release(ctl, id: str, cron: str, *, timezone: str | None = None, enabled: bool = True, kind="schedule"):
    trigger = {"kind": kind, "cron": cron, "timezone": timezone, "inputs": {}} if kind == "schedule" else {"kind": kind}
    ctl.ctx.docs.put(RELEASES, id, {"id": id, "process_id": "p1", "commit": "c" * 40, "image": "wynd/p1:c",
                                    "trigger": trigger, "env": {}, "enabled": enabled, "created_at": T0.isoformat(),
                                    "last_fire_key": None, "last_fired_at": None})


@pytest.fixture
def ctl(controller):
    controller.releases = RecordingReleases(controller)
    return controller


def make_scheduler(ctl, start=T0, **kw) -> Scheduler:
    return Scheduler(ctl, clock=lambda: start, **kw)


def ticks(scheduler: Scheduler, minutes) -> list[str]:
    fired = []
    for n in minutes:
        fired += scheduler.tick(minute(n))
    scheduler.pool.shutdown(wait=True)
    return fired


def test_fires_exactly_once_per_matching_minute(ctl):
    put_release(ctl, "rel_a", "*/5 * * * *")
    scheduler = make_scheduler(ctl)

    fired = []
    for n in range(1, 11):
        fired += scheduler.tick(minute(n))
    fired += scheduler.tick(minute(10))                         # the same minute again
    fired += scheduler.tick(minute(10) + timedelta(seconds=30))
    scheduler.pool.shutdown(wait=True)

    assert fired == ["rel_a", "rel_a"]
    assert [(key, source) for _, key, source in ctl.releases.fired] == \
        [(minute(5).isoformat(), "schedule"), (minute(10).isoformat(), "schedule")]
    doc = ctl.ctx.docs.get(RELEASES, "rel_a")
    assert doc["last_fire_key"] == "2026-09-22T12:10:00+00:00"
    assert datetime.fromisoformat(doc["last_fired_at"]) == T0


def test_the_fire_key_is_persisted_before_firing_so_a_crash_does_not_fire_twice(ctl):
    put_release(ctl, "rel_a", "3 * * * *")
    ctl.releases.fail = RuntimeError("controller crashed mid-fire")

    assert ticks(make_scheduler(ctl), [3]) == ["rel_a"]
    [(_, key_at_fire, _)] = ctl.releases.fired
    assert key_at_fire == minute(3).isoformat()                 # written before trigger ran

    restarted = make_scheduler(ctl, start=minute(2))            # a new scheduler sees minute 3 again
    assert ticks(restarted, [3]) == []
    assert len(ctl.releases.fired) == 1


def test_an_earlier_minute_than_the_last_fire_never_fires(ctl):
    put_release(ctl, "rel_a", "* * * * *")
    ctl.ctx.docs.update(RELEASES, "rel_a", {"last_fire_key": minute(5).isoformat()})

    assert ticks(make_scheduler(ctl, start=minute(2)), [4]) == []


def test_disabled_and_unscheduled_releases_are_skipped(ctl):
    put_release(ctl, "rel_off", "* * * * *", enabled=False)
    put_release(ctl, "rel_manual", "", kind="manual")
    put_release(ctl, "rel_hook", "", kind="webhook")
    put_release(ctl, "rel_on", "* * * * *")

    assert ticks(make_scheduler(ctl), [1]) == ["rel_on"]


def test_an_unusable_schedule_is_skipped_and_the_others_still_fire(ctl):
    put_release(ctl, "rel_bad_cron", "61 * * * *")
    put_release(ctl, "rel_bad_zone", "* * * * *", timezone="Mars/Olympus")
    put_release(ctl, "rel_ok", "* * * * *")

    assert ticks(make_scheduler(ctl), [1]) == ["rel_ok"]


def test_each_release_fires_at_most_once_per_tick(ctl):
    put_release(ctl, "rel_a", "* * * * *")

    assert ticks(make_scheduler(ctl), [4]) == ["rel_a"]         # minutes 1..4 all match
    assert ctl.ctx.docs.get(RELEASES, "rel_a")["last_fire_key"] == minute(1).isoformat()


def test_catch_up_is_bounded(ctl):
    put_release(ctl, "rel_a", "3 12 * * *")

    assert ticks(make_scheduler(ctl), [10]) == []               # 12:03 is more than 5 minutes behind 12:10
    assert ticks(make_scheduler(ctl), [7]) == ["rel_a"]         # 12:03 is within (12:02, 12:07]
    assert ticks(make_scheduler(ctl, max_catch_up_minutes=10), [10]) == []   # already fired at 12:03


def test_schedules_run_in_the_release_timezone(ctl):
    put_release(ctl, "rel_london", "0 9 * * *", timezone="Europe/London")
    put_release(ctl, "rel_utc", "0 9 * * *")
    at_8_utc = datetime(2026, 9, 22, 7, 59, tzinfo=UTC)
    scheduler = make_scheduler(ctl, start=at_8_utc)

    fired = scheduler.tick(at_8_utc + timedelta(minutes=1))     # 08:00 UTC = 09:00 BST
    fired += scheduler.tick(at_8_utc + timedelta(minutes=61))   # 09:00 UTC
    scheduler.pool.shutdown(wait=True)

    assert fired == ["rel_london", "rel_utc"]


def test_an_ambiguous_local_minute_fires_once(ctl):
    put_release(ctl, "rel_a", "30 1 * * *", timezone="Europe/London")
    start = datetime(2026, 10, 25, 0, 0, tzinfo=UTC)            # 01:00 BST; the clocks go back at 01:00 UTC
    scheduler = make_scheduler(ctl, start=start)

    fired = []
    for n in range(1, 121):                                     # 00:01 .. 02:00 UTC covers 01:30 BST and 01:30 GMT
        fired += scheduler.tick(start + timedelta(minutes=n))
    scheduler.pool.shutdown(wait=True)

    assert fired == ["rel_a"]
    assert ctl.ctx.docs.get(RELEASES, "rel_a")["last_fire_key"] == "2026-10-25T00:30:00+00:00"


def test_tick_refreshes_oauth_tokens_first(ctl, monkeypatch):
    calls = []
    monkeypatch.setattr(ctl.registries, "refresh_tokens", lambda: calls.append("refresh") or [])

    ticks(make_scheduler(ctl), [1])

    assert calls == ["refresh"]


def test_a_failing_token_refresh_does_not_stop_the_schedule(ctl, monkeypatch):
    def refresh():
        raise RuntimeError("token endpoint down")

    monkeypatch.setattr(ctl.registries, "refresh_tokens", refresh)
    put_release(ctl, "rel_a", "* * * * *")

    assert ticks(make_scheduler(ctl), [1]) == ["rel_a"]


def test_a_second_scheduler_cannot_take_the_lock(ctl):
    first, second = make_scheduler(ctl), make_scheduler(ctl)

    assert first.start() is True
    try:
        assert second.start() is False
    finally:
        first.stop()
    assert second.start() is True
    second.stop()
    assert (ctl.ctx.state_dir / "locks" / "scheduler.lock").is_file()


def test_started_scheduler_ticks_on_its_own(ctl, monkeypatch):
    import wynd.controller.releases.scheduler as scheduler_module

    monkeypatch.setattr(scheduler_module, "_until_next_minute", lambda now: 0.01)
    put_release(ctl, "rel_a", "* * * * *")
    now = [T0]
    scheduler = Scheduler(ctl, clock=lambda: now[0])
    assert scheduler.start()
    try:
        now[0] = minute(1)
        assert ctl.releases.done.acquire(timeout=5)
    finally:
        scheduler.stop()

    assert [release for release, _, _ in ctl.releases.fired] == ["rel_a"]


# --- the scheduler trigger backend --------------------------------------------------------------------------------------

def backend_kw(ctl) -> dict:
    return {"env": ctl.ctx.env, "workspace_root": ctl.ctx.root, "state_dir": ctl.ctx.state_dir,
            "stores": ctl.ctx.stores}


def test_open_trigger_backend_selects_the_entry_point(ctl):
    backend = open_trigger_backend("scheduler", **backend_kw(ctl))
    assert isinstance(backend, SchedulerTriggers) and backend.name == "scheduler"
    with pytest.raises(Invalid) as caught:
        open_trigger_backend("cron-daemon", **backend_kw(ctl))
    assert "unknown trigger backend 'cron-daemon'" in caught.value.message and "scheduler" in caught.value.message


def test_the_default_trigger_backend_is_the_scheduler(controller):
    assert isinstance(controller.ctx.triggers, SchedulerTriggers)


def test_scheduler_triggers_start_the_scheduler_and_only_one_runs(ctl):
    first, second = SchedulerTriggers(**backend_kw(ctl)), SchedulerTriggers(**backend_kw(ctl))
    for call in (lambda b: b.install(None), lambda b: b.remove("rel_x"), lambda b: b.reconcile([])):
        assert call(first) is None                              # no-ops: the scheduler reads the records

    first.start(ctl)
    try:
        assert first.scheduler is not None
        second.start(ctl)
        assert second.scheduler is None                         # the lock is held
    finally:
        first.stop()
    assert first.scheduler is None
    second.start(ctl)
    assert second.scheduler is not None
    second.stop()


def test_the_scheduler_fires_through_the_release_service(release_ctl):
    """Within the controller: a due schedule release is fired by `tick` through `ReleaseService.trigger` with the
    schedule's fixed inputs."""
    ctl, release = release_ctl
    scheduler = Scheduler(ctl, clock=lambda: T0)
    fired = scheduler.tick(minute(1))
    scheduler.stop()
    for thread in list(ctl.releases.threads):
        thread.join(5)

    assert fired == [release.id]
    [fire] = ctl.releases.fires(release.id)
    assert (fire.source, fire.ok) == ("schedule", True)
    run = ctl.runs.get(fire.run_id)
    assert (run.trigger, run.status, run.inputs) == ("schedule", "succeeded", {"text": "scheduled"})


@pytest.fixture
def release_ctl(workspace, make_controller):
    from support.ctl_m2_runapi import FakeRunApi
    from support.ctl_rel_fakes import FakeServing, FakeTriggers, put_build
    from wynd.controller.api.models_web import CreateReleaseRequest, ScheduleTrigger
    from wynd.controller.releases.service import ReleaseService
    from wynd.controller.status import process_head
    from wynd.spec.env_manifest import EnvManifest

    api = FakeRunApi()
    ctl = make_controller(workspace)
    ctl.releases = ReleaseService(ctl.ctx, ctl, run_api_client=api)
    install_backends(ctl, FakeServing(api), FakeTriggers())
    head = process_head(ctl.ctx, "p1")[0]
    put_build(ctl, "p1", head, EnvManifest(process="p1", vars=[]))
    trigger = ScheduleTrigger(cron="* * * * *", inputs={"text": "scheduled"})
    release = ctl.releases.create(CreateReleaseRequest(process_id="p1", commit=head, trigger=trigger))
    for thread in list(ctl.releases.threads):
        thread.join(5)
    return ctl, release
