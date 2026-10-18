"""Sample-aligned capture regressions. No audio devices, models or cloud calls."""
from __future__ import annotations

import contextlib
import io
import math
import struct
import sys
import threading
import unittest
from array import array
from collections import deque
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from meetingvar.capture.audio import AudioCapture, DualCapture, to_pcm16k

RATE = 16000
EPOCH = 1_790_000_000.0


def pcm(value, samples):
    return struct.pack('<h', value) * samples


def capture():
    source = AudioCapture.__new__(AudioCapture)
    source._ring = deque()
    source._lock = threading.Lock()
    source._audio_origin_ts = None
    source._captured_samples = 0
    source._last_audio_end = None
    source._state = None
    source._dev = {"name": "synthetic source"}
    source.session_start = EPOCH
    return source


def dual(loop, mic, lag=0.35):
    combined = DualCapture.__new__(DualCapture)
    combined._loop, combined._mic = loop, mic
    combined.session_start = EPOCH
    combined._mix_origin = None
    combined._source_progress = {}
    combined._max_source_lag = lag
    combined._stopped = False
    return combined


class CaptureTimelineTests(unittest.TestCase):
    def test_dual_start_timestamp_matches_mixed_sample_zero(self):
        loop, mic = capture(), capture()
        loop._ring.append((EPOCH + 0.01, pcm(100, 1600)))
        mic._ring.append((EPOCH + 0.03, pcm(200, 1600)))
        combined = dual(loop, mic)
        data, start = combined.ingest_since(EPOCH - 1)
        self.assertAlmostEqual(start, EPOCH + 0.01, places=5)
        self.assertEqual(len(data) // 2, 1600)
        self.assertEqual(array('h', data)[:320], array('h', [100] * 320))
        self.assertEqual(array('h', data)[320:], array('h', [300] * 1280))

    def test_mid_block_cursor_reads_tail_instead_of_dropping_whole_block(self):
        source = capture()
        source._ring.append((EPOCH, array('h', range(1600)).tobytes()))
        source._ring.append((EPOCH + 0.1, pcm(2000, 1600)))
        data, start = source.ingest_since(EPOCH + 0.075)
        self.assertEqual(array('h', data)[:400], array('h', range(1200, 1600)))
        self.assertEqual(len(data) // 2, 2000)
        self.assertAlmostEqual(start, EPOCH + 0.075, places=5)

    def test_single_source_returns_contiguous_runs_without_erasing_real_gap(self):
        source = capture()
        source._ring.extend([(EPOCH, pcm(100, 1600)), (EPOCH + 0.2, pcm(200, 1600))])
        first, start = source.ingest_since(EPOCH)
        self.assertEqual(len(first) // 2, 1600)
        second, next_start = source.ingest_since(start + len(first) / 32000)
        self.assertEqual(second, pcm(200, 1600))
        self.assertAlmostEqual(next_start - (start + .1), .1, places=5)

    def test_span_pcm_trims_both_edges_and_keeps_silence_for_gaps(self):
        source = capture()
        source._ring.extend([(EPOCH, pcm(100, 1600)), (EPOCH + .2, pcm(200, 1600))])
        out = array('h', source.span_pcm(EPOCH + .05, EPOCH + .25))
        self.assertEqual(out, array('h', [100] * 800 + [0] * 1600 + [200] * 800))

    def test_single_anchor_uses_integer_samples_for_ninety_minutes(self):
        source = capture()
        source._ring = deque(maxlen=2)
        block = pcm(123, 1600)
        for i in range(54000):
            source._append_pcm(block, EPOCH + (i + 1) / 10)
        self.assertEqual(source._captured_samples, 86_400_000)
        self.assertLess(abs(source._ring[-1][0] - (EPOCH + 5399.9)), .5 / RATE)
        self.assertLess(abs(source._last_audio_end - (EPOCH + 5400)), .5 / RATE)

    def test_capture_read_error_resets_resampler_and_preserves_real_gap(self):
        from types import SimpleNamespace
        from meetingvar.capture import audio as audio_module
        source = capture()
        source._stop = threading.Event()
        source._rate = RATE
        source._channels = 1
        source._frames_per_read = 1600
        source._session_clock = 10.0
        reads = []
        def read(count, exception_on_overflow):
            reads.append(exception_on_overflow)
            if len(reads) == 2:
                raise OSError('synthetic input overflow')
            if len(reads) == 3:
                source._stop.set()
            return pcm(100 * len(reads), 1600)
        source._stream = SimpleNamespace(read=read)
        with patch.object(audio_module, 'time', SimpleNamespace(monotonic=lambda: 10.1 if len(reads) == 1 else 10.5)), \
             contextlib.redirect_stdout(io.StringIO()) as messages:
            source._loop()
        self.assertEqual(reads, [True, True, True])
        first, second = list(source._ring)
        self.assertAlmostEqual(second[0] - (first[0] + .1), .3, places=5)
        self.assertIn('中断', messages.getvalue())
        self.assertIn('恢复', messages.getvalue())

    def test_resampling_state_and_timestamps_follow_actual_output_count(self):
        source = capture()
        state = None
        total = 0
        for i in range(1000):
            mono, state = to_pcm16k(pcm(300, 4410), 1, 44100, state)
            source._append_pcm(mono, EPOCH + (i + 1) / 10)
            total += len(mono) // 2
        self.assertEqual(source._captured_samples, total)
        self.assertLess(abs(source._last_audio_end - (source._audio_origin_ts + total / RATE)), .5 / RATE)

    def test_staggered_dual_frames_never_skip_or_duplicate_samples(self):
        loop, mic = capture(), capture()
        loop._ring = deque(maxlen=3)
        mic._ring = deque(maxlen=3)
        combined = dual(loop, mic)
        cursor, count = EPOCH - 1, 0
        for i in range(400):
            loop._ring.append((EPOCH + .01 + i * .1, pcm(100, 1600)))
            mic._ring.append((EPOCH + .03 + i * .1, pcm(200, 1600)))
            data, start = combined.ingest_since(cursor)
            self.assertTrue(data)
            self.assertLess(abs(start - (EPOCH + .01 + count / RATE)), .5 / RATE)
            values = array('h', data)
            expected = [100] * min(320, len(values)) + [300] * max(0, len(values) - 320) if i == 0 else [300] * len(values)
            self.assertEqual(values, array('h', expected))
            count += len(data) // 2
            cursor = start + len(data) / 32000
        self.assertEqual(count, 400 * 1600)
        combined._stopped = True
        tail, start = combined.ingest_since(cursor)
        self.assertEqual(tail, pcm(200, 320))
        self.assertLess(abs(start - cursor), .5 / RATE)
        again, _ = combined.ingest_since(start + len(tail) / 32000)
        self.assertFalse(again)

    def test_late_source_waits_without_consuming_faster_tail(self):
        loop, mic = capture(), capture()
        combined = dual(loop, mic)
        loop._ring.append((EPOCH, pcm(100, 1600)))
        with patch('meetingvar.capture.audio.time.monotonic', return_value=10):
            out, _ = combined.ingest_since(EPOCH - 1)
        self.assertFalse(out)
        mic._ring.append((EPOCH, pcm(200, 1600)))
        with patch('meetingvar.capture.audio.time.monotonic', return_value=10.1):
            out, start = combined.ingest_since(EPOCH - 1)
        self.assertEqual(out, pcm(300, 1600))
        self.assertEqual(start, EPOCH)

    def test_stalled_source_is_reported_and_recovery_keeps_continuity(self):
        loop, mic = capture(), capture()
        combined = dual(loop, mic)
        loop._ring.append((EPOCH, pcm(100, 1600)))
        with patch('meetingvar.capture.audio.time.monotonic', return_value=10):
            self.assertFalse(combined.ingest_since(EPOCH - 1)[0])
        with patch('meetingvar.capture.audio.time.monotonic', return_value=10.5), contextlib.redirect_stdout(io.StringIO()) as messages:
            out, start = combined.ingest_since(EPOCH - 1)
        self.assertEqual(out, pcm(100, 1600))
        self.assertIn('音频', messages.getvalue())
        loop._ring.append((EPOCH + .1, pcm(100, 1600)))
        mic._ring.append((EPOCH + .1, pcm(200, 1600)))
        with patch('meetingvar.capture.audio.time.monotonic', return_value=10.6), contextlib.redirect_stdout(io.StringIO()):
            out, next_start = combined.ingest_since(start + len(out) / 32000)
        self.assertEqual(out, pcm(300, 1600))
        self.assertLess(abs(next_start - (EPOCH + .1)), .5 / RATE)

    def test_real_gap_in_both_sources_is_not_masked_by_false_silence(self):
        loop, mic = capture(), capture()
        combined = dual(loop, mic)
        loop._ring.append((EPOCH, pcm(100, 1600)))
        mic._ring.append((EPOCH, pcm(200, 1600)))
        out, start = combined.ingest_since(EPOCH)
        loop._ring.append((EPOCH + 1, pcm(100, 1600)))
        mic._ring.append((EPOCH + 1, pcm(200, 1600)))
        next_out, next_start = combined.ingest_since(start + len(out) / 32000)
        self.assertEqual(next_out, pcm(300, 1600))
        self.assertEqual(next_start, EPOCH + 1)

    def test_stopped_mixer_drains_disjoint_runs_before_longer_other_source(self):
        loop, mic = capture(), capture()
        loop._ring.extend([(EPOCH, pcm(100, 1600)), (EPOCH + .2, pcm(200, 1600))])
        mic._ring.append((EPOCH, pcm(1000, 6400)))
        combined = dual(loop, mic)
        combined._stopped = True
        cursor, chunks = EPOCH, []
        for _ in range(5):
            data, start = combined.ingest_since(cursor)
            if not data:
                break
            self.assertLess(abs(start - cursor), .5 / RATE)
            chunks.append(data)
            cursor = start + len(data) / 32000
        self.assertEqual(b''.join(chunks), pcm(1100, 1600) + pcm(1000, 1600) + pcm(1200, 1600) + pcm(1000, 1600))
        self.assertAlmostEqual(cursor, EPOCH + .4, places=5)

    def test_dual_capture_to_real_sender_runs_ninety_minutes_in_one_session(self):
        import asyncio
        import hashlib
        import json
        from types import SimpleNamespace
        from meetingvar.capture import audio as audio_module
        from meetingvar.transcribe import xfyun_rtasr as rt
        loop, mic = capture(), capture()
        loop._ring = deque(maxlen=4)
        mic._ring = deque(maxlen=4)
        combined = dual(loop, mic)
        count = 54000
        cursor = EPOCH - 1
        expected = hashlib.sha256()
        actual = hashlib.sha256()
        clock = SimpleNamespace(now=0.0)
        received = 0
        with patch.object(rt, 'load_creds', return_value={}):
            client = rt.XfyunRealtime('unused', connect=lambda *a, **k: self.fail('No network permitted'))
        client._started = True
        self.addCleanup(client.stop)
        state = rt._Connection('dual-90-minute')

        def outputs():
            nonlocal cursor
            previous_mic = 0
            for i in range(count):
                lv, mv = i % 1000 + 1, -2 * (i % 1000 + 1)
                expected.update(pcm(lv + previous_mic, 320))
                expected.update(pcm(lv + mv, 1280))
                previous_mic = mv
                for source, value, phase in [(loop, lv, .11), (mic, mv, .13)]:
                    clock.now = i / 10 + phase
                    source._append_pcm(pcm(value, 1600), EPOCH + clock.now)
                    data, start = combined.ingest_since(cursor)
                    if data:
                        cursor = start + len(data) / 32000
                        yield data, start
            expected.update(pcm(previous_mic, 320))
            combined._stopped = True
            data, start = combined.ingest_since(cursor)
            if data:
                yield data, start
        stream = outputs()
        def feed_next():
            try:
                data, start = next(stream)
                client.feed(data, start)
            except StopIteration:
                client._finishing = True
        class SenderClock:
            now = 0.0
            def time(self): return self.now
        send_clock = SenderClock()
        ends = []
        async def sleep(delay):
            send_clock.now += delay
        async def send(ws, payload):
            nonlocal received
            if isinstance(payload, bytes):
                actual.update(payload)
                received += len(payload) // 2
                while not client._finishing and client._pending_bytes - len(payload) < rt.FRAME_BYTES:
                    feed_next()
            else:
                ends.append(json.loads(payload))
        async def run():
            client._wake = asyncio.Event()
            with patch.object(rt.asyncio, 'get_running_loop', return_value=send_clock), \
                 patch.object(rt.asyncio, 'sleep', new=sleep), patch.object(client, '_send', new=send):
                return await client._send_audio(object(), state)
        with patch.object(audio_module, 'time', SimpleNamespace(monotonic=lambda: clock.now)):
            feed_next()
            self.assertEqual(asyncio.run(run()), 'finish')
        self.assertEqual(received, count * 1600 + 320)
        self.assertEqual(actual.hexdigest(), expected.hexdigest())
        self.assertEqual(ends, [{'end': True}])
        self.assertEqual(client.poll(), [])
        self.assertLess(abs(state.origin - (EPOCH + .01)), .5 / RATE)
        self.assertLess(abs(state.origin + state.sent_bytes / 32000 - (EPOCH + 5400.03)), .5 / RATE)

    def test_microphone_unavailable_preserves_loopback_contract(self):
        loop = capture()
        loop._ring.append((EPOCH, pcm(120, 1600)))
        combined = dual(loop, None)
        out, start = combined.ingest_since(EPOCH - 1)
        self.assertEqual(out, pcm(120, 1600))
        self.assertEqual(start, EPOCH)


if __name__ == '__main__':
    unittest.main()
