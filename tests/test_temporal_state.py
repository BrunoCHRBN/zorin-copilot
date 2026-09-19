"""Testes unitários para o gerenciamento de estado temporal e decaimento contínuo."""

from __future__ import annotations

import time
import unittest
from zorin_copilot.core.temporal_state import TemporalState, TemporalStateManager


class TestTemporalState(unittest.TestCase):
    def test_state_initialization(self):
        state = TemporalState(dimension=16)
        self.assertEqual(len(state.hidden_vector), 16)
        self.assertTrue(all(v == 0.0 for v in state.hidden_vector))

    def test_recurrent_update(self):
        state = TemporalState(dimension=4)
        input_vec = [1.0, -1.0, 0.5, 0.0]
        now = 1000.0
        updated = state.update(input_vec, current_ts=now)

        self.assertEqual(len(updated), 4)
        # tanh(0.3 * 1.0) approx 0.2913
        self.assertAlmostEqual(updated[0], 0.2913, places=2)
        self.assertAlmostEqual(updated[1], -0.2913, places=2)

    def test_exponential_decay_over_time(self):
        state = TemporalState(dimension=2, decay_lambda=0.1)
        now = 1000.0
        state.update([1.0, 1.0], current_ts=now)

        # Sem passagem de tempo
        same_time = state.get_decayed_state(current_ts=now)
        self.assertAlmostEqual(same_time[0], state.hidden_vector[0], places=4)

        # Após 10 segundos (fator exp(-0.1 * 10) = exp(-1) approx 0.3678)
        later = now + 10.0
        decayed = state.get_decayed_state(current_ts=later)
        expected = state.hidden_vector[0] * 0.367879
        self.assertAlmostEqual(decayed[0], expected, places=3)

    def test_state_reset_on_screen_lock(self):
        manager = TemporalStateManager(dimension=8)
        manager.register_event([0.8] * 8)
        self.assertNotEqual(manager.get_current_state(), [0.0] * 8)

        # Simula travamento de tela
        manager.on_screen_locked()
        self.assertEqual(manager.get_current_state(), [0.0] * 8)


if __name__ == "__main__":
    unittest.main()
