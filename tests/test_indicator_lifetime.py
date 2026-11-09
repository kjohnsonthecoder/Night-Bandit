"""Progress repeats must not multiply callbacks or leave timers after Stop/Exit."""
import tkinter as tk
import unittest
from types import SimpleNamespace
from unittest.mock import patch
from night_bandit_desktop.experience import Indicator

class IndicatorLifetimeTests(unittest.TestCase):
    def setUp(self):self.root=tk.Tk();self.root.withdraw();self.indicator=Indicator(self.root)
    def tearDown(self):self.root.destroy()
    def timers(self):return set(self.root.tk.call('after','info'))
    def test_repeated_real_phase_updates_have_one_timer_and_stop_cancels_it(self):
        baseline=self.timers()
        with patch('night_bandit_desktop.experience.motion_for',return_value=SimpleNamespace(enabled=lambda:True)):
            for _ in range(50):self.indicator.start()
            self.assertEqual(len(self.timers()-baseline),1)
            self.indicator.stop();self.assertEqual(self.timers(),baseline)
    def test_destroy_after_repeated_start_leaves_no_callback(self):
        baseline=self.timers()
        with patch('night_bandit_desktop.experience.motion_for',return_value=SimpleNamespace(enabled=lambda:True)):
            self.indicator.start();self.indicator.start();self.indicator.start()
            self.indicator.destroy();self.assertEqual(self.timers(),baseline)
            self.root.update()

if __name__=='__main__':unittest.main()
