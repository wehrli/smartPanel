"""Notification kinds, actions and Do Not Disturb (SP-2501, SP-2503).

No real bubble is ever shown: every Notifier here gets a recording backend, and
the libnotify adapter is driven with a stand-in for the `Notify` gi module.
"""
import pytest

from smartpanel import notify
from smartpanel.notify import KINDS, Action, Notifier


class Recorder(notify._Backend):
    def __init__(self):
        self.shown = []

    def show(self, summary, body, urgency, key, icon=None, action=None):
        self.shown.append(dict(summary=summary, body=body, urgency=urgency, key=key,
                               icon=icon, action=action))
        return True


def make(banners=True):
    rec = Recorder()
    return Notifier(backend=rec, banners_enabled=lambda: banners), rec


def test_every_kind_has_key_and_urgency():
    assert set(KINDS) == {"swap_warning", "swap_critical", "swap_ok", "journal_ok",
                          "journal_failed", "budget_80", "budget_100"}
    assert KINDS["swap_critical"].urgency == "critical"
    for k in KINDS.values():
        assert k.urgency in ("low", "normal", "critical") and k.key


def test_send_kind_uses_its_policy():
    n, rec = make()
    assert n.send("swap_critical", "Swap critically full", "93% used") is True
    assert rec.shown[0] == dict(summary="Swap critically full", body="93% used",
                                urgency="critical", key="swap", icon="dialog-error",
                                action=None)


def test_swap_kinds_share_a_bubble_budget_and_journal_have_their_own():
    keys = {k: v.key for k, v in KINDS.items()}
    assert keys["swap_warning"] == keys["swap_critical"] == keys["swap_ok"] == "swap"
    assert keys["budget_80"] == keys["budget_100"] == "budget"
    assert keys["journal_ok"] == keys["journal_failed"] == "journal"


def test_key_override_and_action_passthrough():
    n, rec = make()
    act = Action("View", lambda: None)
    n.send("journal_ok", "Journal written", "ok", action=act, key="journal:claude-x")
    assert rec.shown[0]["key"] == "journal:claude-x"
    assert rec.shown[0]["action"] is act


@pytest.mark.parametrize("kind", sorted(set(KINDS) - {"swap_critical"}))
def test_dnd_holds_everything_but_critical_swap(kind):
    n, rec = make(banners=False)
    assert n.send(kind, "t", "b") is False
    assert rec.shown == []


def test_dnd_lets_critical_swap_through():
    n, rec = make(banners=False)
    assert n.send("swap_critical", "Swap critically full", "b") is True
    assert len(rec.shown) == 1


def test_broken_dnd_probe_means_banners_on():
    def boom():
        raise RuntimeError("no schema")
    rec = Recorder()
    n = Notifier(backend=rec, banners_enabled=boom)
    assert n.send("journal_ok", "t") is True


def test_legacy_positional_form_still_works():
    n, rec = make()
    n.send("Swap almost full", "Swap is 90% full.", "critical", key="swap")
    assert rec.shown[0]["summary"] == "Swap almost full"
    assert rec.shown[0]["body"] == "Swap is 90% full."
    assert rec.shown[0]["urgency"] == "critical"


def test_no_backend_and_failing_backend_never_raise():
    n = Notifier(backend=None, banners_enabled=lambda: True)
    assert n.send("swap_ok", "t") is False

    class Boom(notify._Backend):
        def show(self, *a, **k):
            raise OSError("dbus gone")
    n = Notifier(backend=Boom(), banners_enabled=lambda: True)
    assert n.send("swap_ok", "t") is False


def test_gnome_banners_probe_does_not_crash():
    # Whatever this machine has (schema present or not), the probe answers a bool.
    assert notify._GnomeBanners()() in (True, False)


# ── libnotify adapter, with a stand-in for gi.repository.Notify ──

class FakeNote:
    def __init__(self, summary, body, icon):
        self.args = (summary, body, icon)
        self.actions, self.urgency, self.shows, self.cleared = [], None, 0, 0

    def update(self, summary, body, icon):
        self.args = (summary, body, icon)

    def clear_actions(self):
        self.actions, self.cleared = [], self.cleared + 1

    def set_urgency(self, u):
        self.urgency = u

    def add_action(self, action_id, label, cb, user_data):
        self.actions.append((action_id, label, cb))

    def show(self):
        self.shows += 1


class FakeNotify:
    class Urgency:
        LOW, NORMAL, CRITICAL = "L", "N", "C"

    class Notification:
        made = []

        @classmethod
        def new(cls, summary, body, icon):
            note = FakeNote(summary, body, icon)
            cls.made.append(note)
            return note

    caps = ["actions", "body"]

    @classmethod
    def get_server_caps(cls):
        return cls.caps


def test_libnotify_reuses_handle_per_key_and_wires_actions():
    FakeNotify.Notification.made = []
    FakeNotify.caps = ["actions", "body"]
    be = notify._LibNotify(FakeNotify)
    clicks = []
    be.show("Swap is filling up", "66%", "normal", "swap",
            action=Action("Open System", lambda: clicks.append(1)))
    be.show("Swap critically full", "93%", "critical", "swap")
    assert len(FakeNotify.Notification.made) == 1          # same bubble
    note = FakeNotify.Notification.made[0]
    assert note.args[0] == "Swap critically full" and note.urgency == "C"
    assert note.cleared == 1 and note.actions == []        # stale button removed
    be.show("Swap is filling up", "66%", "normal", "swap",
            action=Action("Open System", lambda: clicks.append(1)))
    ids = [a[0] for a in note.actions]
    assert "open" in ids and "default" in ids
    note.actions[0][2](note, "open", None)                 # click
    assert clicks == [1]


def test_libnotify_without_actions_capability_drops_buttons():
    FakeNotify.Notification.made = []
    FakeNotify.caps = ["body"]
    be = notify._LibNotify(FakeNotify)
    be.show("t", "b", "low", "k", action=Action("View", lambda: None))
    assert FakeNotify.Notification.made[0].actions == []


def test_action_callback_errors_are_contained():
    def bad():
        raise ValueError("x")
    notify._guarded(Action("View", bad))(None, "open", None)   # must not raise
