import unittest

from floating_pomodoro import LONG_BREAK, SHORT_BREAK, WORK, Pomodoro


class FakeClock:
    def __init__(self):
        self.t = 0.0

    def __call__(self):
        return self.t


class PomodoroTest(unittest.TestCase):
    def setUp(self):
        self.clock = FakeClock()
        self.p = Pomodoro(work=25, short_break=5, long_break=15, long_every=4, clock=self.clock)

    def test_starts_paused_on_work(self):
        self.assertEqual(self.p.phase, WORK)
        self.assertFalse(self.p.running)
        self.assertEqual(self.p.remaining(), 25 * 60)

    def test_counts_down_and_pauses(self):
        self.p.start()
        self.clock.t += 60
        self.assertEqual(self.p.remaining(), 24 * 60)
        self.p.pause()
        self.clock.t += 300
        self.assertEqual(self.p.remaining(), 24 * 60)

    def test_phase_cycle_with_long_break(self):
        phases = []
        for _ in range(8):
            self.p.start()
            self.clock.t += self.p.remaining() + 1
            self.assertTrue(self.p.tick())
            phases.append(self.p.phase)
        self.assertEqual(phases, [SHORT_BREAK, WORK, SHORT_BREAK, WORK, SHORT_BREAK, WORK, LONG_BREAK, WORK])
        self.assertEqual(self.p.completed, 4)

    def test_next_phase_waits_unless_auto_start(self):
        self.p.start()
        self.clock.t += 25 * 60
        self.p.tick()
        self.assertFalse(self.p.running)
        auto = Pomodoro(auto_start=True, clock=self.clock)
        auto.start()
        self.clock.t += 25 * 60
        auto.tick()
        self.assertTrue(auto.running)

    def test_adjust_only_while_paused_and_min_one_minute(self):
        self.p.adjust(5)
        self.assertEqual(self.p.remaining(), 30 * 60)
        self.assertEqual(self.p.progress(), 0)
        self.p.adjust(-100)
        self.assertEqual(self.p.remaining(), 60)
        self.p.start()
        self.p.adjust(10)
        self.assertEqual(self.p.remaining(), 60)

    def test_reset(self):
        self.p.start()
        self.clock.t += 600
        self.p.reset()
        self.assertFalse(self.p.running)
        self.assertEqual(self.p.remaining(), 25 * 60)


if __name__ == "__main__":
    unittest.main()
