"""Testes unitários para o gerenciador de perfis de energia (PowerManager)."""

from __future__ import annotations

import unittest
from zorin_copilot.core.power_manager import PowerManager, PowerProfile


class TestPowerManager(unittest.TestCase):
    def test_singleton_instance(self):
        pm1 = PowerManager.get_default()
        pm2 = PowerManager.get_default()
        self.assertIs(pm1, pm2)

    def test_profiles_configuration(self):
        pm = PowerManager()
        ac_profile = pm._build_profile(is_battery=False)
        self.assertFalse(ac_profile.is_battery)
        self.assertEqual(ac_profile.stream_sample_rate_hz, 60.0)
        self.assertTrue(ac_profile.enable_proactive_predictions)

        bat_profile = pm._build_profile(is_battery=True)
        self.assertTrue(bat_profile.is_battery)
        self.assertEqual(bat_profile.stream_sample_rate_hz, 10.0)
        self.assertFalse(bat_profile.enable_proactive_predictions)
        self.assertLess(bat_profile.idle_sleep_sec, ac_profile.idle_sleep_sec)

    def test_listener_notification(self):
        pm = PowerManager()
        received_profiles = []

        def on_change(profile: PowerProfile):
            received_profiles.append(profile)

        pm.add_listener(on_change)

        # Força alternância de estado
        original_state = pm._is_battery
        pm._is_battery = not original_state
        pm._profile = pm._build_profile(not original_state)
        pm._notify_listeners()

        self.assertEqual(len(received_profiles), 1)
        self.assertEqual(received_profiles[0].is_battery, not original_state)

        pm.remove_listener(on_change)


if __name__ == "__main__":
    unittest.main()
