import unittest

from chalkak.picker_geometry import match_outputs


class MonitorMatchTest(unittest.TestCase):
    def test_subset_of_translated_scaled_xwayland_outputs(self):
        left, right = (-120, -40, 120, 80), (0, 0, 80, 60)
        left_x, right_x = (0, 0, 240, 160), (240, 80, 160, 120)
        granted = [("HDMI-1", right)]
        outputs = [("XWAYLAND1", right_x), ("XWAYLAND0", left_x)]
        self.assertEqual(match_outputs(granted, [left, right], outputs), [right_x])

    def test_duplicate_geometry_requires_unique_output_name(self):
        r = (0, 0, 200, 100)
        outputs = [("HDMI-1", r), ("HDMI-2", r)]
        with self.assertRaisesRegex(ValueError, "위치"):
            match_outputs([("unknown", r)], [r, r], outputs)

    def test_changed_layout_fails_closed(self):
        r = (0, 0, 200, 100)
        with self.assertRaisesRegex(ValueError, "변경"):
            match_outputs([("HDMI-1", r)], [r], [])


if __name__ == "__main__":
    unittest.main()
