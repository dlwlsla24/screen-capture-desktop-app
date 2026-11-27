"""Exercise the one-frame reader with real Gst buffers and synthetic video sources."""

import os
import time
import unittest
from unittest.mock import Mock, patch

import gi
gi.require_version("Gst", "1.0")
gi.require_version("GstVideo", "1.0")
from gi.repository import GLib, Gst, GstVideo

from chalkak import capture_frame as frame
from chalkak.capture_selection import AreaSelection


Gst.init(None)
LEFT = (-4, -2, 4, 4)
RIGHT = (0, 0, 4, 4)


def callback_loop():
    loop = GLib.MainLoop()
    values, errors = [], []

    def collect(target):
        def callback(value):
            target.append(value)
            loop.quit()
        return callback

    return loop, values, errors, collect(values), collect(errors)


def selection(image, regions):
    monitors = []
    for x, y, w, h in regions:
        monitor = Mock()
        monitor.get_geometry.return_value = Mock(x=x, y=y, width=w, height=h)
        monitors.append(monitor)
    with patch("chalkak.capture_selection.Gdk.Display.get_default") as display:
        display.return_value.get_monitors.return_value = monitors
        return AreaSelection(image, Mock(), Mock())


class SnapshotTest(unittest.TestCase):
    def read(self, streams, factory, timeout=False):
        loop, values, errors, ready, error = callback_loop()
        fd = os.open(os.devnull, os.O_RDONLY)
        pipelines = []

        def make(gst, remote_fd, node):
            self.assertEqual(fd, remote_fd)
            pipeline = Gst.parse_launch(factory(node))
            pipelines.append(pipeline)
            return pipeline

        with patch.object(frame, "monitor_layout", return_value=[LEFT, RIGHT]), \
             patch.object(frame, "_pipeline", side_effect=make), \
             patch.object(frame, "FRAME_TIMEOUT", 0 if timeout else frame.FRAME_TIMEOUT):
            reader = frame.read_snapshot(fd, streams, ready, error)
            self.assertFalse(values or errors)
            guard = GLib.timeout_add_seconds(3, loop.quit)
            loop.run()
            self.assertEqual(len(values) + len(errors), 1)
            GLib.source_remove(guard)
            reader.cancel()
        with self.assertRaises(OSError):
            os.fstat(fd)
        for p in pipelines:
            self.assertEqual(p.get_state(0).state, Gst.State.NULL)
        return (values or [None])[0], errors

    @staticmethod
    def pattern(node):
        size, color = (8, "red") if node == 42 else (4, "blue")
        return (f"videotestsrc num-buffers=1 pattern={color} ! "
                f"video/x-raw,format=RGB,width={size},height={size} ! "
                "appsink name=frame sync=false")

    def test_scaled_negative_origins_and_gap(self):
        image, errors = self.read([(42, {"position": LEFT[:2]}),
                                   (43, {"position": RIGHT[:2]})], self.pattern)
        self.assertFalse(errors)
        self.assertEqual((image.get_width(), image.get_height()), (16, 12))
        area = selection(image, [LEFT, RIGHT])
        self.assertIsNone(area.crop(-2, -1, 2, 1))
        crop = area.crop(-1, 0, 1, 1)
        self.assertEqual(crop.get_width(), 4)
        self.assertEqual(crop.get_pixels()[:3], b"\xff\0\0")
        self.assertEqual(crop.get_pixels()[9:12], b"\0\0\xff")

    def test_only_selected_monitor_has_window_and_correct_origin(self):
        image, errors = self.read([(43, {"position": RIGHT[:2]})], self.pattern)
        self.assertFalse(errors)
        area = selection(image, [LEFT, RIGHT])
        self.assertEqual(len(area.monitors), 1)
        self.assertEqual((area.left, area.top), RIGHT[:2])
        self.assertEqual(area.crop(1, 1, 2, 2).get_pixels(), b"\0\0\xff")
        with self.assertRaisesRegex(ValueError, "변경"):
            selection(image, [LEFT])

    def test_ambiguous_and_partial_failure(self):
        _, errors = self.read([(42, {"size": LEFT[2:]})], self.pattern)
        self.assertIn("모두 선택", errors[0])
        _, errors = self.read([(42, {"position": LEFT[:2]}), (43, {"position": RIGHT[:2]})],
                              lambda n: self.pattern(n) if n == 42 else "no_such_element")
        self.assertTrue(errors)

    def test_timeout_closes_pipeline(self):
        _, errors = self.read([(43, {"position": RIGHT[:2]})],
                              lambda n: "appsrc ! appsink name=frame", timeout=True)
        self.assertIn("초과", errors[0])

    def test_static_monitor_is_nudged_until_its_frame_arrives(self):
        # GNOME sends no frame for a monitor whose contents never change. The
        # reader must poke only the monitors that are still missing.
        loop, values, errors, ready, error = callback_loop()
        fd = os.open(os.devnull, os.O_RDONLY)
        static = []
        nudged = []

        def make(gst, remote_fd, node):
            if node == 42:
                return Gst.parse_launch(self.pattern(node))
            pipeline = Gst.parse_launch(
                "appsrc name=src caps=video/x-raw,format=RGB,width=4,height=4,framerate=0/1 ! "
                "appsink name=frame sync=false")
            static.append(pipeline)
            return pipeline

        def nudge(regions):
            nudged.append(list(regions))
            if len(nudged) == 2:  # the compositor repaints only after being poked
                static[0].get_by_name("src").emit("push-buffer", Gst.Buffer.new_wrapped(bytes(48)))

        with patch.object(frame, "monitor_layout", return_value=[LEFT, RIGHT]), \
             patch.object(frame, "_pipeline", side_effect=make), \
             patch.object(frame, "NUDGE_FIRST", 0.01), patch.object(frame, "NUDGE_EVERY", 0.02):
            reader = frame.read_snapshot(
                fd, [(42, {"position": LEFT[:2]}), (43, {"position": RIGHT[:2]})], ready, error, nudge)
            guard = GLib.timeout_add_seconds(3, loop.quit)
            loop.run()
            GLib.source_remove(guard)
            reader.cancel()
        self.assertFalse(errors)
        self.assertEqual(len(values), 1)
        self.assertGreaterEqual(len(nudged), 2)
        self.assertTrue(all(regions == [RIGHT] for regions in nudged))

    def test_failing_nudge_does_not_break_the_read(self):
        loop, values, errors, ready, error = callback_loop()
        fd = os.open(os.devnull, os.O_RDONLY)
        with patch.object(frame, "monitor_layout", return_value=[LEFT, RIGHT]), \
             patch.object(frame, "_pipeline", side_effect=lambda g, f, n: Gst.parse_launch("appsrc ! appsink name=frame")), \
             patch.object(frame, "NUDGE_FIRST", 0.0), patch.object(frame, "NUDGE_EVERY", 0.01), \
             patch.object(frame, "FRAME_TIMEOUT", 0.15):
            reader = frame.read_snapshot(fd, [(43, {"position": RIGHT[:2]})], ready, error,
                                         Mock(side_effect=OSError("helper gone")))
            guard = GLib.timeout_add_seconds(3, loop.quit)
            loop.run()
            GLib.source_remove(guard)
            reader.cancel()
        self.assertFalse(values)
        self.assertIn("초과", errors[0])

    def test_deadline_starts_after_pipelines_play(self):
        # 첫 캡처는 Gst 초기화·파이프라인 시작이 오래 걸릴 수 있다. 그 시간은 프레임 대기
        # 한도에 넣지 않는다: 한도는 모든 파이프라인이 PLAYING 이 된 뒤부터 센다.
        fd = os.open(os.devnull, os.O_RDONLY)
        slow = 0.3
        pipeline = Gst.parse_launch("appsrc ! appsink name=frame")

        def make(gst, remote_fd, node):
            time.sleep(slow)
            return pipeline

        callback = Mock()
        with patch.object(frame, "monitor_layout", return_value=[RIGHT]), \
             patch.object(frame, "_pipeline", side_effect=make):
            requested = time.monotonic()
            reader = frame.read_snapshot(fd, [(43, {})], callback, callback)
            self.assertIsNone(reader.deadline)
            while reader.deadline is None:
                GLib.MainContext.default().iteration(True)
            started = time.monotonic()
            reader.cancel()
        self.assertEqual(pipeline.get_state(0).state, Gst.State.NULL)
        self.assertGreaterEqual(reader.deadline, requested + slow + frame.FRAME_TIMEOUT)
        self.assertLessEqual(reader.deadline, started + frame.FRAME_TIMEOUT)
        callback.assert_not_called()

    def test_cancel_before_idle(self):
        fd = os.open(os.devnull, os.O_RDONLY)
        ready = Mock()
        reader = frame.read_snapshot(fd, [], ready, ready)
        reader.cancel()
        reader.cancel()
        GLib.MainContext.default().iteration(False)
        ready.assert_not_called()
        with self.assertRaises(OSError):
            os.fstat(fd)

    def test_pipewire_pipeline_targets_portal_node(self):
        with open(os.devnull, "rb") as remote:
            pipe = frame._pipeline(Gst, remote.fileno(), 17)
            status, source = pipe.iterate_sources().next()
            self.assertEqual(status, Gst.IteratorResult.OK)
            self.assertEqual(source.get_factory().get_name(), "pipewiresrc")
            self.assertEqual(source.get_property("path"), "17")
            self.assertEqual(source.get_property("fd"), remote.fileno())
            self.assertEqual(source.get_property("num-buffers"), 1)

    def test_cancel_while_waiting(self):
        fd = os.open(os.devnull, os.O_RDONLY)
        callback = Mock()
        pipeline = Gst.parse_launch("appsrc ! appsink name=frame")
        with patch.object(frame, "monitor_layout", return_value=[RIGHT]), \
             patch.object(frame, "_pipeline", return_value=pipeline):
            reader = frame.read_snapshot(fd, [(43, {})], callback, callback)
            while not reader.pipelines:
                GLib.MainContext.default().iteration(True)
            reader.cancel()
        self.assertEqual(pipeline.get_state(0).state, Gst.State.NULL)
        callback.assert_not_called()
        with self.assertRaises(OSError):
            os.fstat(fd)

    def test_video_meta_offset_and_stride(self):
        caps = Gst.Caps.from_string("video/x-raw,format=RGB,width=1,height=2,framerate=1/1")
        for stride, offset, expected in [(5, 2, b"xyz"), (-5, 7, b"abc")]:
            buf = Gst.Buffer.new_wrapped(b"--abc..xyz")
            GstVideo.buffer_add_video_meta_full(buf, GstVideo.VideoFrameFlags.NONE,
                                                GstVideo.VideoFormat.RGB, 1, 2, 1,
                                                [offset, 0, 0, 0], [stride, 0, 0, 0])
            pixbuf = frame._copy_sample(Gst.Sample.new(buf, caps, None, None), GstVideo)
            self.assertEqual(pixbuf.new_subpixbuf(0, 1, 1, 1).get_pixels(), expected)
