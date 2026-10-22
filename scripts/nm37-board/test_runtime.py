"""Runtime boundary tests with numpy and a mock engine, never MMIO/devices."""
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch
import board_test as b
try:
    import numpy as np
except ImportError:
    np = None


@unittest.skipIf(np is None, 'Run with the FSA Python environment to test array/runtime behavior')
class RuntimeTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.addCleanup(self.tmp.cleanup)
        self.runtime = b.Runtime.__new__(b.Runtime)
        r = self.runtime
        r.np = np
        r.ref = np.zeros((16, 16), dtype=np.float32)
        r.F = SimpleNamespace(to_numpy=lambda result: result)
        r.kernel = SimpleNamespace(input=[SimpleNamespace(data=b'input'), SimpleNamespace(data=None)])
        r.original_data = [b'input', None]
        r.instruction_word_count = 10
        self.counters = {key: i for i, key in enumerate(b.COUNTERS)}
        self.counters.update(execTime=7700, enq=0, deq=0)
        self.state = 0
        r.engine = SimpleNamespace(MMIO=Mock(), STATE_OFFSET=8)
        self.addresses = {12 + 4*i: self.counters[key] for i, key in enumerate(b.COUNTERS)}
        for i, attr in enumerate(b.COUNTERS.values()):
            setattr(r.engine, attr, 12 + 4*i)
        r.engine.MMIO.dev_mmio_read.side_effect = lambda addr: self.state if addr == 8 else self.addresses[addr]
        def execute(kernel):
            self.assertEqual(kernel.input[0].data, b'input')
            self.assertIsNone(kernel.input[1].data)
            kernel.input[1].data = b'previous output must not be uploaded on repeat'
            self.state = 2
            self.addresses[44] += 10
            self.addresses[48] += 10
            return r.ref.T.copy()
        r.engine.execute = Mock(side_effect=execute)

    def test_execute_time_counter_units_and_repeat_input_restore(self):
        for index in range(2):
            out = self.root / str(index)
            out.mkdir()
            with patch.object(b.time, 'monotonic_ns', side_effect=[1_000_000, 3_000_000]):
                result = self.runtime.execute(out, 70)
            self.assertTrue(result['passed'])
            self.assertEqual(result['exec_us'], 110)
            self.assertEqual(result['host_execute_ms'], 2)
            self.assertEqual(len(result['counters']), 10)
            self.assertEqual(result['state_before'], 0 if index == 0 else 2)

    def test_active_state_refuses_input_writes(self):
        self.state = 1
        with self.assertRaises(RuntimeError):
            self.runtime.execute(self.root, 100)
        self.runtime.engine.execute.assert_not_called()

    def test_bitwise_negative_zero_difference_fails(self):
        def execute(kernel):
            self.state = 2
            self.addresses[44] += 10
            self.addresses[48] += 10
            result = self.runtime.ref.T.copy()
            result[0, 0] = np.float32(-0.0)
            return result
        self.runtime.engine.execute.side_effect = execute
        result = self.runtime.execute(self.root, 110)
        self.assertFalse(result['passed'])
        self.assertEqual(result['bit_mismatches'], 1)

    def test_enq_deq_mismatch_fails(self):
        original = self.runtime.engine.execute.side_effect
        def execute(kernel):
            output = original(kernel)
            self.addresses[48] -= 1
            return output
        self.runtime.engine.execute.side_effect = execute
        result = self.runtime.execute(self.root, 110)
        self.assertFalse(result['passed'])
        self.assertEqual(result['bit_mismatches'], 0)

    def test_cumulative_word_count_wrap_uses_per_run_delta(self):
        self.addresses[44] = self.addresses[48] = 2**32 - 5
        original = self.runtime.engine.execute.side_effect
        def execute(kernel):
            output = original(kernel)
            self.addresses[44] %= 2**32
            self.addresses[48] %= 2**32
            return output
        self.runtime.engine.execute.side_effect = execute
        result = self.runtime.execute(self.root, 110)
        self.assertTrue(result['passed'])
        self.assertEqual(result['instruction_word_deltas'], {'enq': 10, 'deq': 10})
        self.assertEqual(result['counters']['enq'], 5)

    def test_wrong_submitted_word_count_fails_even_if_enq_equals_deq(self):
        self.runtime.instruction_word_count = 11
        result = self.runtime.execute(self.root, 110)
        self.assertFalse(result['passed'])

    def test_outstanding_words_refuses_input_writes(self):
        self.addresses[44] = 1
        with self.assertRaises(RuntimeError):
            self.runtime.execute(self.root, 110)
        self.runtime.engine.execute.assert_not_called()

    def test_postrun_state_change_refuses_counters(self):
        self.runtime.engine.execute.side_effect = lambda kernel: self.runtime.ref.T.copy()
        with self.assertRaises(RuntimeError):
            self.runtime.execute(self.root, 110)


if __name__ == '__main__':
    unittest.main()
