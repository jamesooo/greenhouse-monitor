import os
import sys
import unittest
from unittest.mock import patch

from greenhouse.greenhouse_monitor import (
    CameraTuning,
    GreenhouseMonitor,
    apply_camera_tuning,
    parse_args,
)


class CameraTuningTests(unittest.TestCase):
    def test_manual_controls_disable_their_automatic_modes(self):
        tuning = CameraTuning(
            brightness=4,
            auto_white_balance=False,
            white_balance_temperature=5100,
            power_line_frequency=2,
            auto_exposure=False,
            exposure_time=200,
            dynamic_framerate=True,
        )

        self.assertEqual(
            tuning.v4l2_controls(),
            {
                "brightness": 4,
                "power_line_frequency": 2,
                "exposure_dynamic_framerate": 1,
                "white_balance_automatic": 0,
                "white_balance_temperature": 5100,
                "auto_exposure": 1,
                "exposure_time_absolute": 200,
            },
        )

    def test_manual_values_enable_manual_modes_when_unspecified(self):
        tuning = CameraTuning(
            white_balance_temperature=4600,
            exposure_time=156,
        )

        controls = tuning.v4l2_controls()

        self.assertEqual(controls["white_balance_automatic"], 0)
        self.assertEqual(controls["auto_exposure"], 1)

    def test_conflicting_automatic_and_manual_settings_are_rejected(self):
        with self.assertRaisesRegex(ValueError, "white balance"):
            CameraTuning(
                auto_white_balance=True,
                white_balance_temperature=4600,
            ).v4l2_controls()

        with self.assertRaisesRegex(ValueError, "exposure"):
            CameraTuning(
                auto_exposure=True,
                exposure_time=156,
            ).v4l2_controls()

    @patch("greenhouse.greenhouse_monitor.subprocess.run")
    def test_apply_camera_tuning_uses_v4l2_control_names(self, run):
        tuning = CameraTuning(contrast=32, sharpness=3)

        apply_camera_tuning(tuning)

        run.assert_called_once_with(
            [
                "v4l2-ctl",
                "--device",
                "/dev/video0",
                "--set-ctrl",
                "contrast=32,sharpness=3",
            ],
            check=True,
            capture_output=True,
            text=True,
        )

    @patch("greenhouse.greenhouse_monitor.subprocess.run")
    def test_no_controls_skips_v4l2_command(self, run):
        apply_camera_tuning(CameraTuning())
        run.assert_not_called()

    def test_image_capture_request_sets_wakeup_event(self):
        monitor = GreenhouseMonitor([], None, unittest.mock.Mock(), "/tmp")
        self.addCleanup(monitor.executor.shutdown)

        monitor.request_image_capture()

        self.assertTrue(monitor._image_capture_requested.is_set())

    @patch.dict(
        os.environ,
        {
            "GREENHOUSE_CAMERA_PIXEL_FORMAT": "yuyv",
            "GREENHOUSE_CAMERA_POWER_LINE_FREQUENCY": "60",
            "GREENHOUSE_CAMERA_AUTO_WHITE_BALANCE": "false",
            "GREENHOUSE_CAMERA_WHITE_BALANCE_TEMPERATURE": "5000",
            "GREENHOUSE_CAMERA_AUTO_EXPOSURE": "false",
            "GREENHOUSE_CAMERA_EXPOSURE_TIME": "200",
            "GREENHOUSE_CAMERA_WARMUP_FRAMES": "20",
            "GREENHOUSE_CAMERA_WARMUP_DELAY": "0.2",
        },
        clear=True,
    )
    @patch.object(sys, "argv", ["greenhouse-monitor"])
    def test_camera_tuning_environment_values_are_parsed(self):
        args = parse_args()

        self.assertEqual(args.camera_pixel_format, "YUYV")
        self.assertEqual(args.camera_power_line_frequency, 2)
        self.assertFalse(args.camera_auto_white_balance)
        self.assertEqual(args.camera_white_balance_temperature, 5000)
        self.assertFalse(args.camera_auto_exposure)
        self.assertEqual(args.camera_exposure_time, 200)
        self.assertEqual(args.camera_warmup_frames, 20)
        self.assertEqual(args.camera_warmup_delay, 0.2)


if __name__ == "__main__":
    unittest.main()