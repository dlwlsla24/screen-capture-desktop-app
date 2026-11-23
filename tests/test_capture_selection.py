"""Desktop-to-image mapping, including reversed drags and monitor seams."""

import unittest
from unittest.mock import Mock, patch

from chalkak.capture_selection import AreaSelection
from chalkak.util import GdkPixbuf


def monitor(x, y, width, height):
    result = Mock()
    result.get_geometry.return_value = Mock(x=x, y=y, width=width, height=height)
    return result


class CaptureGeometryTest(unittest.TestCase):
    def setUp(self):
        # Two logical 100x80 monitors with a 40-pixel vertical offset and 2x pixels.
        pixbuf = GdkPixbuf.Pixbuf.new(GdkPixbuf.Colorspace.RGB, False, 8, 400, 240)
        pixbuf.fill(0x112233ff)
        patch_color = pixbuf.new_subpixbuf(180, 120, 40, 20)
        patch_color.fill(0x122334ff)
        with patch("chalkak.capture_selection.Gdk.Display.get_default") as display:
            display.return_value.get_monitors.return_value = [
                monitor(-100, -40, 100, 80), monitor(0, 0, 100, 80)
            ]
            self.selection = AreaSelection(pixbuf, Mock(), Mock())

    def test_reversed_drag_across_seam(self):
        crop = self.selection.crop(10, 30, -10, 20)
        self.assertEqual((crop.get_width(), crop.get_height()), (40, 20))
        self.assertEqual(bytes(crop.get_pixels()[:3]), bytes.fromhex("122334"))

    def test_clips_outside_desktop(self):
        crop = self.selection.crop(-200, -140, -80, -30)
        self.assertEqual((crop.get_width(), crop.get_height()), (40, 20))

    def test_zero_area_cancels_without_selection(self):
        self.assertIsNone(self.selection.crop(5, 10, 5, 25))


if __name__ == "__main__":
    unittest.main()
