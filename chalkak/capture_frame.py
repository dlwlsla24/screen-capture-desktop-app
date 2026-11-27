"""One-shot reads from monitor nodes on an already authorized PipeWire remote."""

import os
import time

import gi

gi.require_version("Gdk", "4.0")
gi.require_version("GdkPixbuf", "2.0")
from gi.repository import Gdk, GdkPixbuf, GLib

# GNOME 은 화면이 바뀐 모니터의 프레임만 보낸다. 가만히 멈춰 있는 모니터는 끝내 프레임이
# 오지 않으므로, 아직 안 온 모니터를 일정 간격으로 "다시 그리게" 건드린다.
NUDGE_FIRST = 0.06
NUDGE_EVERY = 0.10
FRAME_TIMEOUT = 5


def monitor_layout():
    display = Gdk.Display.get_default()
    if not display:
        raise ValueError("사용 가능한 모니터가 없습니다.")
    return [geometry(m) for m in display.get_monitors()]


def geometry(monitor):
    r = monitor.get_geometry()
    return r.x, r.y, r.width, r.height


def _match_streams(streams, monitors):
    """Portal position and size are in logical compositor coordinates."""
    positions = {}
    for node, props in streams:
        position = props.get("position")
        size = props.get("size")
        candidates = [r for r in monitors
                      if (position is None or tuple(position) == r[:2])
                      and (size is None or tuple(size) == r[2:])]
        if (not isinstance(node, int) or not 0 < node < 2**32 - 1
                or props.get("source_type", 1) != 1 or node in positions
                or len(candidates) != 1 or candidates[0] in positions.values()):
            raise ValueError("공유 화면의 모니터 위치를 확인할 수 없습니다. 모니터를 모두 선택해 주세요.")
        positions[node] = candidates[0]
    if not positions:
        raise ValueError("공유할 모니터를 선택해 주세요.")
    return positions


def _compose(frames, positions):
    regions = tuple(positions.values())
    left, top = min(r[0] for r in regions), min(r[1] for r in regions)
    right = max(x + w for x, y, w, h in regions)
    bottom = max(y + h for x, y, w, h in regions)
    # Keep the highest source density when the rectangle spans mixed-DPI displays.
    scale = max(max(frames[n].get_width() / w, frames[n].get_height() / h)
                for n, (_, _, w, h) in positions.items())
    width, height = round((right - left) * scale), round((bottom - top) * scale)
    if len(frames) == 1:
        image = next(iter(frames.values()))
    else:
        image = GdkPixbuf.Pixbuf.new(GdkPixbuf.Colorspace.RGB, False, 8, width, height)
        image.fill(0)
        for n, (x, y, w, h) in positions.items():
            frame = frames[n]
            # Round shared edges identically to AreaSelection.crop.
            x0 = round((x - left) * width / (right - left))
            x1 = round((x + w - left) * width / (right - left))
            y0 = round((y - top) * height / (bottom - top))
            y1 = round((y + h - top) * height / (bottom - top))
            frame.scale(image, x0, y0, x1 - x0, y1 - y0, x0, y0,
                        (x1 - x0) / frame.get_width(), (y1 - y0) / frame.get_height(),
                        GdkPixbuf.InterpType.BILINEAR)
    image.monitor_regions = regions
    return image


def _copy_sample(sample, GstVideo):
    info = GstVideo.VideoInfo.new_from_caps(sample.get_caps())
    buf = sample.get_buffer()
    meta = GstVideo.buffer_get_video_meta(buf)
    layout = meta or info
    stride, offset = layout.stride[0], layout.offset[0]
    w, h = info.width, info.height
    if (w <= 0 or h <= 0 or abs(stride) < 3 * w
            or min(offset, offset + (h - 1) * stride) < 0
            or max(offset, offset + (h - 1) * stride) + 3 * w > buf.get_size()):
        raise ValueError("화면 프레임의 픽셀 배치가 올바르지 않습니다.")
    if stride > 0:
        data = buf.extract_dup(offset, (h - 1) * stride + 3 * w)
    else:
        data = b"".join(buf.extract_dup(offset + y * stride, 3 * w) for y in range(h))
        stride = 3 * w
    return GdkPixbuf.Pixbuf.new_from_bytes(GLib.Bytes.new(data), GdkPixbuf.Colorspace.RGB,
                                          False, 8, w, h, stride)


def _pipeline(Gst, fd, node):
    # path takes the portal node ID. target-object expects a name or object serial.
    # All sources use the same fd so the PipeWire plugin shares its connection.
    return Gst.parse_launch(
        f"pipewiresrc fd={fd} path={node} num-buffers=1 ! videoconvert ! "
        "video/x-raw,format=RGB ! appsink name=frame sync=false max-buffers=1"
    )


class _Snapshot:
    def __init__(self, fd, streams, ready, error, nudge=None):
        self.fd = fd
        self.streams = streams
        self.ready, self.error = ready, error
        self.nudge = nudge  # nudge(regions): 그 모니터들을 다시 그리게 한다
        self.next_nudge = 0.0
        self.pipelines = {}
        self.frames = {}
        self.deadline = None  # 파이프라인을 모두 시작한 뒤(_start 끝)에 잡는다
        self.source = GLib.idle_add(self._start)

    def _start(self):
        self.source = 0
        try:
            if not isinstance(self.fd, int) or self.fd < 0:
                self.fd = None
                raise ValueError("공유 화면 연결을 열 수 없습니다.")
            self.positions = _match_streams(self.streams, monitor_layout())
            gi.require_version("Gst", "1.0")
            gi.require_version("GstVideo", "1.0")
            from gi.repository import Gst, GstVideo
            Gst.init(None)
            self.gst, self.video = Gst, GstVideo
            for node in self.positions:
                pipeline = _pipeline(Gst, self.fd, node)
                self.pipelines[node] = pipeline
                if pipeline.set_state(Gst.State.PLAYING) == Gst.StateChangeReturn.FAILURE:
                    raise ValueError("공유 화면 스트림을 시작할 수 없습니다.")
            # 첫 캡처의 Gst 초기화(레지스트리 재스캔)·파이프라인 시작 시간은 프레임 대기 한도에서 뺀다.
            now = time.monotonic()
            self.deadline = now + FRAME_TIMEOUT
            self.next_nudge = now + NUDGE_FIRST
            self.source = GLib.timeout_add(15, self._poll)
        except Exception as exc:
            self._finish(self.error, str(exc))
        return GLib.SOURCE_REMOVE

    def _poll(self):
        try:
            for node, pipeline in self.pipelines.items():
                if node in self.frames:
                    continue
                sink = pipeline.get_by_name("frame")
                message = pipeline.get_bus().pop_filtered(
                    self.gst.MessageType.ERROR | self.gst.MessageType.EOS)
                if message and message.type == self.gst.MessageType.ERROR:
                    raise ValueError(message.parse_error()[0].message)
                sample = sink.emit("try-pull-sample", 0)
                if sample is not None:
                    self.frames[node] = _copy_sample(sample, self.video)
                elif message:
                    raise ValueError("공유 화면에서 프레임을 받지 못했습니다.")
            if len(self.frames) < len(self.positions):
                now = time.monotonic()
                if now >= self.deadline:
                    raise TimeoutError("공유 화면 응답 시간이 초과되었습니다.")
                if self.nudge is not None and now >= self.next_nudge:
                    self.next_nudge = now + NUDGE_EVERY
                    self._nudge_missing()
                return GLib.SOURCE_CONTINUE
            result = _compose(self.frames, self.positions)
        except Exception as exc:
            self._finish(self.error, str(exc))
        else:
            self._finish(self.ready, result)
        return GLib.SOURCE_REMOVE

    def _nudge_missing(self):
        regions = [region for node, region in self.positions.items() if node not in self.frames]
        try:
            self.nudge(regions)
        except Exception:  # noqa: BLE001  건드리지 못해도 화면이 바뀌면 프레임은 온다
            pass

    def _finish(self, callback, value):
        self.source = 0  # the running GLib source removes itself on return
        self.cancel()
        callback(value)

    def cancel(self):
        if self.source:
            GLib.source_remove(self.source)
            self.source = 0
        for pipeline in self.pipelines.values():
            pipeline.set_state(self.gst.State.NULL)
        self.pipelines.clear()
        self.frames.clear()
        if self.fd is not None:
            os.close(self.fd)
            self.fd = None
        self.ready = self.error = self.nudge = None


def read_snapshot(remote_fd, streams, on_ready, on_error, nudge=None):
    """Consume remote_fd; deliver once on GTK's main loop unless cancelled there."""
    return _Snapshot(remote_fd, streams, on_ready, on_error, nudge)
