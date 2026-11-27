"""Portal session ownership, persistence negotiation and one-shot delivery."""

import itertools
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from gi.repository import Gio, GLib

from chalkak.capture import NativeCapture
from chalkak.screencast import CAST, CHOOSER_SETTLE_MS, DESKTOP, REQUEST, SESSION, ScreenCast


class PortalLifecycleTest(unittest.TestCase):
    def setUp(self):
        self.bus = Mock()
        self.bus.get_unique_name.return_value = ":1.21"
        self.bus.is_closed.return_value = False
        self.bus.signal_subscribe.side_effect = itertools.count(1)
        self.props = {"version": GLib.Variant("u", 4),
                      "AvailableSourceTypes": GLib.Variant("u", 1)}
        self.outputs = ["HDMI-1"]
        self.bus.call_sync.side_effect = self.sync_call
        self.ready, self.cancel, self.error = Mock(), Mock(), Mock()
        self.cast = ScreenCast(self.ready, self.cancel, self.error)
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.cast.token_path = Path(tmp.name, "chalkak", "screencast-restore-token")
        for name, value in (("dbus_address_get_for_bus_sync", "test"),
                            ("DBusConnection.new_for_address_sync", self.bus)):
            patcher = patch("chalkak.screencast.Gio." + name, return_value=value)
            patcher.start()
            self.addCleanup(patcher.stop)
        self.session = DESKTOP + "/session/1_21/test"

    def sync_call(self, destination, path, interface, method, *args):
        if method == "GetCurrentState":
            self.assertEqual(destination, "org.gnome.Mutter.DisplayConfig")
            self.assertEqual(path, "/org/gnome/Mutter/DisplayConfig")
            logical = [(i * 3840, 0, 1.0, 0, i == 0,
                        [(port, "Vendor", "Same Model", "0")], {})
                       for i, port in enumerate(self.outputs)]
            return GLib.Variant("(uaa{sv}a(iiduba(ssss)a{sv})a{sv})",
                                (1, [], logical, {}))
        return GLib.Variant("(a{sv})", (self.props,))

    def next_capture(self, **kwargs):
        path = self.cast.token_path
        self.cast.close()
        self.cast = ScreenCast(self.ready, self.cancel, self.error, **kwargs)
        self.cast.token_path = path
        self.create()
        return self.bus.call.call_args.args[4].unpack()[1]

    def legacy(self, token):
        self.cast.token_path.parent.mkdir(mode=0o700)
        self.cast.token_path.write_text(token)

    def respond(self, method, code, results):
        args = self.bus.call.call_args.args
        self.assertEqual(args[2:4], (CAST, method))
        path = args[-1]
        subscription = self.bus.signal_subscribe.call_args.args
        self.assertEqual(subscription[3], path)
        self.bus.call_finish.return_value = GLib.Variant("(o)", (path,))
        args[-2](self.bus, object(), path)
        subscription[-2](self.bus, None, path, None, None,
                         GLib.Variant("(ua{sv})", (code, results)), subscription[-1])

    def create(self):
        self.cast.start()
        self.respond("CreateSession", 0, {"session_handle": GLib.Variant("s", self.session)})

    def start_remote(self, nodes=(42,)):
        self.respond("SelectSources", 0, {})
        self.respond("Start", 0, {"streams": GLib.Variant("a(ua{sv})", [(n, {}) for n in nodes]),
                                  "restore_token": GLib.Variant("s", "replacement")})

    def test_restores_rotates_token_and_closes_before_overlay(self):
        self.legacy("previous")
        self.create()
        self.assertEqual(self.bus.call.call_args.args[4].unpack()[1]["restore_token"], "previous")
        options = self.bus.call.call_args.args[4].unpack()[1]
        self.assertEqual((options["persist_mode"], options["types"], options["multiple"]),
                         (2, 1, True))
        self.start_remote()
        saved = json.loads(self.cast.token_path.read_text())
        self.assertEqual(saved, {"version": 1, "monitors": [["HDMI-1", "Vendor", "Same Model", "0"]],
                                 "token": "replacement"})
        self.assertEqual(self.cast.token_path.stat().st_mode & 0o777, 0o600)
        self.assertEqual(self.cast.token_path.parent.stat().st_mode & 0o777, 0o700)
        remote = self.bus.call_with_unix_fd_list.call_args.args
        self.assertEqual(remote[3], "OpenPipeWireRemote")
        fds = Gio.UnixFDList.new()
        with open(os.devnull, "rb") as dummy:
            index = fds.append(dummy.fileno())
        self.bus.call_with_unix_fd_list_finish.return_value = GLib.Variant("(h)", (index,)), fds
        with patch("chalkak.screencast.read_snapshot") as read:
            remote[-2](self.bus, object(), remote[-1])
            fd, streams, ready, error = read.call_args.args
            os.close(fd)
            self.assertEqual(streams, [(42, {})])
            self.ready.side_effect = lambda pixbuf: self.bus.close.assert_called_once()
            ready("image")
            error("late callback")
            read.return_value.cancel.assert_called_once()
        self.assertIn((self.session, SESSION, "Close"),
                      [call.args[1:4] for call in self.bus.call.call_args_list])
        self.ready.assert_called_once_with("image")
        self.error.assert_not_called()

    def test_first_consent_waits_for_chooser_to_disappear(self):
        self.create()
        with patch("chalkak.screencast.GLib.timeout_add", return_value=987) as timer:
            self.start_remote()
        self.bus.call_with_unix_fd_list.assert_not_called()
        delay, callback, streams = timer.call_args.args
        self.assertEqual(delay, CHOOSER_SETTLE_MS)
        self.assertEqual(callback(streams), GLib.SOURCE_REMOVE)
        self.assertEqual(self.cast.settle_source, 0)
        self.bus.call_with_unix_fd_list.assert_called_once()

    def test_added_display_prompts_again_after_cancel_then_restores_both(self):
        self.create()
        self.start_remote()
        original = self.cast.token_path.read_bytes()
        self.outputs.append("DP-3")
        options = self.next_capture()
        self.assertNotIn("restore_token", options)
        self.assertTrue(options["multiple"])
        self.respond("SelectSources", 0, {})
        self.respond("Start", 1, {})
        self.cancel.assert_called_once()
        self.assertEqual(original, self.cast.token_path.read_bytes())
        self.assertNotIn("restore_token", self.next_capture())
        self.start_remote((42, 43))
        self.outputs.reverse()
        self.assertEqual(self.next_capture()["restore_token"], "replacement")
        self.outputs.remove("HDMI-1")
        self.assertNotIn("restore_token", self.next_capture())

    def test_legacy_on_two_displays_prompts_and_preserves_chosen_subset(self):
        self.legacy("old")
        self.outputs.append("DP-3")
        self.assertNotIn("restore_token", self.next_capture())
        self.start_remote((42,))
        self.assertNotIn("restore_token", self.next_capture(reselect=True))
        self.respond("SelectSources", 1, {})
        self.assertEqual(self.next_capture()["restore_token"], "replacement")
        self.outputs[1] = "DP-4"
        self.assertNotIn("restore_token", self.next_capture())

    def test_chooser_state_lasts_from_tokenless_select_until_start(self):
        # 토큰이 없으면 GNOME 이 모니터 선택 창을 띄운다: 사용자가 고르는 동안만 choosing.
        self.assertFalse(self.cast.choosing)
        self.create()
        self.assertNotIn("restore_token", self.bus.call.call_args.args[4].unpack()[1])
        self.assertTrue(self.cast.choosing)
        self.respond("SelectSources", 0, {})
        self.assertTrue(self.cast.choosing)
        with patch("chalkak.screencast.GLib.timeout_add", return_value=55):
            self.respond("Start", 0, {"streams": GLib.Variant("a(ua{sv})", [(42, {})]),
                                      "restore_token": GLib.Variant("s", "chosen")})
        self.assertFalse(self.cast.choosing)

    def test_cancelled_chooser_clears_state(self):
        self.create()
        self.assertTrue(self.cast.choosing)
        self.respond("SelectSources", 0, {})
        self.respond("Start", 1, {})
        self.cancel.assert_called_once_with()
        self.assertFalse(self.cast.choosing)

    def test_silent_token_restore_is_not_choosing(self):
        self.legacy("previous")
        self.create()
        self.assertEqual(self.bus.call.call_args.args[4].unpack()[1]["restore_token"], "previous")
        self.assertFalse(self.cast.choosing)
        self.respond("SelectSources", 0, {})
        self.assertFalse(self.cast.choosing)

    def test_reselect_and_old_portal_show_the_chooser(self):
        self.legacy("previous")
        self.next_capture(reselect=True)
        self.assertTrue(self.cast.choosing)
        self.props["version"] = GLib.Variant("u", 3)
        self.next_capture()
        self.assertTrue(self.cast.choosing)
        self.cast.close()
        self.assertFalse(self.cast.choosing)

    def test_empty_start_does_not_cache_choice(self):
        self.create()
        self.start_remote(())
        self.error.assert_called_once()
        self.assertFalse(self.cast.token_path.exists())

    def test_hotplug_during_chooser_leaves_next_capture_interactive(self):
        self.create()
        self.outputs.append("DP-3")
        self.start_remote()
        self.assertFalse(self.cast.token_path.exists())
        self.assertNotIn("restore_token", self.next_capture())

    def test_unknown_topology_prompts_without_caching(self):
        self.outputs.clear()
        self.legacy("previous")
        self.assertNotIn("restore_token", self.next_capture())
        self.start_remote()
        self.assertFalse(self.cast.token_path.exists())

    def test_prompted_restore_wait_is_cancelled_on_session_close(self):
        self.legacy("stale")
        self.create()
        with patch("chalkak.screencast.GLib.get_monotonic_time", side_effect=[0, 850000]), \
             patch("chalkak.screencast.GLib.timeout_add", return_value=123) as timer:
            self.start_remote()
        self.bus.call_with_unix_fd_list.assert_not_called()
        with patch("chalkak.screencast.GLib.source_remove") as remove:
            self.cast._session_closed()
            self.cast.close()
        remove.assert_called_once_with(123)
        _, callback, streams = timer.call_args.args
        callback(streams)
        self.bus.call_with_unix_fd_list.assert_not_called()
        self.cancel.assert_called_once()

    def test_old_portal_prompts_without_persistence(self):
        self.props["version"] = GLib.Variant("u", 3)
        self.create()
        options = self.bus.call.call_args.args[4].unpack()[1]
        self.assertNotIn("persist_mode", options)
        self.assertNotIn("restore_token", options)
        self.respond("SelectSources", 0, {})
        self.respond("Start", 1, {})
        self.cancel.assert_called_once_with()
        self.bus.call_with_unix_fd_list.assert_not_called()
        self.assertFalse(self.cast.token_path.exists())

    def test_abandon_pending_request_and_session_together(self):
        self.create()
        request = self.cast.request
        self.cast.close()
        self.cast.close()
        self.assertEqual([call.args[1:4] for call in self.bus.call.call_args_list[-2:]],
                         [(request, REQUEST, "Close"), (self.session, SESSION, "Close")])
        self.bus.close.assert_called_once()
        self.cancel.assert_not_called()
        self.ready.assert_not_called()

    def test_remote_result_after_session_revocation(self):
        self.legacy("previous")
        self.create()
        self.start_remote()
        revocation = next(call.args for call in self.bus.signal_subscribe.call_args_list
                          if call.args[1] == SESSION)
        revocation[-1]()
        fds = Mock()
        self.bus.call_with_unix_fd_list_finish.return_value = GLib.Variant("(h)", (0,)), fds
        remote = self.bus.call_with_unix_fd_list.call_args.args
        remote[-2](self.bus, object(), remote[-1])
        fds.get.assert_not_called()
        self.cancel.assert_called_once_with()
        self.ready.assert_not_called()

    def test_failed_response_closes_without_opening_remote(self):
        self.create()
        self.respond("SelectSources", 2, {})
        self.error.assert_called_once()
        self.bus.close.assert_called_once()
        self.bus.call_with_unix_fd_list.assert_not_called()


class NativeSelectionTest(unittest.TestCase):
    def setUp(self):
        self.done, self.cancel, self.error = Mock(), Mock(), Mock()
        self.capture = NativeCapture(self.done, self.cancel, self.error)
        self.overlay = self.enter_patch("AreaSelection")
        self.save = self.enter_patch("save_png")
        self.capture._snapshot(Mock())
        _, self.release, self.escape, self.failure = self.overlay.call_args.args

    def enter_patch(self, attribute):
        patcher = patch("chalkak.capture." + attribute)
        self.addCleanup(patcher.stop)
        return patcher.start()

    def test_release_saves_clipboard_png_before_preview(self):

        def preview(path):
            self.assertEqual(path, self.save.return_value)
            self.save.assert_called_once_with(b"png")
            self.overlay.return_value.close.assert_called_once_with()

        self.done.side_effect = preview
        self.release(b"png")
        self.release(b"png")
        self.done.assert_called_once()
        self.cancel.assert_not_called()

    def test_escape_closes_without_output(self):
        self.escape()
        self.release(Mock())
        self.overlay.return_value.close.assert_called_once_with()
        self.cancel.assert_called_once_with()
        self.done.assert_not_called()
        self.save.assert_not_called()
        self.error.assert_not_called()


if __name__ == "__main__":
    unittest.main()
