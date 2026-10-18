"""Real-epoch RTASR timeline checks; no capture, credentials, network or threads."""
from __future__ import annotations

import asyncio
import json
import math
import sys
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from meetingvar.transcribe import xfyun_rtasr as rt  # noqa: E402

RATE = 16000
EPOCH = 1700000000.0
BLOCK_SAMPLES = 1600
BLOCK_COUNT = 90 * 60 * 10


class CaptureTimelineTests(unittest.TestCase):
    def client(self):
        # Exercise the existing queue/sender without reading even a synthetic env.
        with mock.patch.object(rt, "load_creds", return_value={}):
            client = rt.XfyunRealtime("unused", connect=self.forbidden_connect)
        client._started = True
        self.addCleanup(client.stop)
        return client

    def forbidden_connect(self, *args, **kwargs):
        self.fail("timeline tests must never open a transport")

    def run_sender(self, client, state, send):
        class Clock:
            now = 0.0

            def time(self):
                return self.now

        clock = Clock()

        async def sleep(delay):
            self.assertGreaterEqual(delay, 0.0)
            clock.now += delay

        async def exercise():
            client._wake = asyncio.Event()
            with mock.patch.object(rt.asyncio, "get_running_loop", return_value=clock), \
                    mock.patch.object(rt.asyncio, "sleep", new=sleep), \
                    mock.patch.object(client, "_send", new=send):
                return await client._send_audio(object(), state)

        return asyncio.run(exercise())

    def test_ninety_minutes_of_100ms_capture_blocks_use_one_session(self):
        client = self.client()
        state = rt._Connection("long-timeline")
        pcm = b"\x01\x02" * BLOCK_SAMPLES
        expected_bytes = BLOCK_COUNT * len(pcm)
        expected_frames = expected_bytes // rt.FRAME_BYTES
        checkpoints = {0, 1, 2, expected_frames // 2, expected_frames - 1}
        observed = {}
        fed_blocks = sent_frames = sent_bytes = end_count = 0
        max_pending = 0

        def feed_next():
            nonlocal fed_blocks, max_pending
            client.feed(pcm, EPOCH + fed_blocks * BLOCK_SAMPLES / RATE)
            fed_blocks += 1
            max_pending = max(max_pending, client._pending_bytes)
            if fed_blocks == BLOCK_COUNT:
                client._finishing = True

        async def send(socket, payload):
            nonlocal sent_frames, sent_bytes, end_count
            if not isinstance(payload, bytes):
                self.assertEqual(json.loads(payload), {"end": True})
                end_count += 1
                return
            self.assertEqual(len(payload), rt.FRAME_BYTES)
            if sent_frames in checkpoints:
                frame, timestamp = client._peek_audio()
                self.assertEqual(payload, frame)
                self.assertEqual(payload, b"\x01\x02" * (rt.FRAME_BYTES // 2))
                observed[sent_frames] = timestamp
            sent_frames += 1
            sent_bytes += len(payload)
            # _send_audio commits only after this callback. Keep at most two
            # capture blocks live, including a frame currently being sent.
            if fed_blocks < BLOCK_COUNT and client._pending_bytes - len(payload) < rt.FRAME_BYTES:
                feed_next()

        feed_next()
        self.assertEqual(self.run_sender(client, state, send), "finish")
        self.assertEqual((fed_blocks, sent_frames), (54000, 135000))
        self.assertEqual((sent_bytes, state.sent_bytes), (expected_bytes, expected_bytes))
        self.assertEqual(state.origin, EPOCH)
        self.assertEqual(end_count, 1)
        self.assertTrue(state.end_sent)
        self.assertEqual(client._pending_bytes, 0)
        self.assertFalse(client._pending)
        self.assertLessEqual(max_pending, 2 * len(pcm))
        self.assertEqual(client.poll(), [])
        self.assertEqual(set(observed), checkpoints)
        for index, timestamp in observed.items():
            self.assertLessEqual(abs(timestamp - (EPOCH + index * rt.FRAME_BYTES / rt.BYTES_PER_SECOND)),
                                 2 * math.ulp(EPOCH))
            self.assertEqual(round((timestamp - EPOCH) * RATE), index * (rt.FRAME_BYTES // 2))
        self.assertEqual(state.origin + state.sent_bytes / rt.BYTES_PER_SECOND, EPOCH + 90 * 60)

    def test_reanchored_sessions_map_sent_integer_bytes_to_capture_samples(self):
        frame_samples = rt.FRAME_BYTES // 2
        frame_count = BLOCK_COUNT * BLOCK_SAMPLES // frame_samples
        worst_error = 0.0
        # A reconnect may anchor inside an existing capture block, so origin
        # itself has the same two additions as _peek_audio's block + offset.
        for first_sample in (0, 1, 319, 1599, BLOCK_COUNT * BLOCK_SAMPLES - 1):
            origin_block, origin_offset = divmod(first_sample, BLOCK_SAMPLES)
            origin = (EPOCH + origin_block * BLOCK_SAMPLES / RATE) + origin_offset / RATE
            with self.subTest(first_sample=first_sample):
                for frame_index in range(frame_count):
                    sent_samples = frame_index * frame_samples
                    block_index, within_block = divmod(first_sample + sent_samples, BLOCK_SAMPLES)
                    block_start = EPOCH + block_index * BLOCK_SAMPLES / RATE
                    peek_timestamp = block_start + within_block / RATE
                    expected = origin + (frame_index * rt.FRAME_BYTES) / rt.BYTES_PER_SECOND
                    error = abs(peek_timestamp - expected)
                    worst_error = max(worst_error, error)
                    self.assertLessEqual(error, 2 * math.ulp(EPOCH))
                    self.assertEqual(round((peek_timestamp - origin) * RATE), sent_samples)
        self.assertLess(worst_error, rt.TIMESTAMP_TOLERANCE)

    def test_two_sample_gap_and_overlap_split_without_losing_pending_audio(self):
        for first_sample in (0, BLOCK_COUNT * BLOCK_SAMPLES - BLOCK_SAMPLES + 1):
            for shift in (-2, 2):
                with self.subTest(first_sample=first_sample, shift_samples=shift):
                    client = self.client()
                    first = b"a" * 640  # a short frame forces _peek_audio to inspect the next block
                    second = b"b" * 640
                    first_start = EPOCH + first_sample / RATE
                    second_start = EPOCH + (first_sample + len(first) // 2 + shift) / RATE
                    client.feed(first, first_start)
                    client.feed(second, second_start)
                    client._finishing = True
                    self.assertEqual(client._peek_audio(), (first, first_start))
                    sent = []

                    async def send(socket, payload):
                        sent.append(payload)

                    previous = rt._Connection("before-gap")
                    self.assertEqual(self.run_sender(client, previous, send), "gap")
                    self.assertEqual(sent, [first, json.dumps({"end": True})])
                    self.assertEqual(previous.origin, first_start)
                    self.assertEqual(previous.sent_bytes, len(first))
                    self.assertEqual(client._pending_bytes, len(second))
                    self.assertEqual(client._peek_audio(), (second, second_start))
                    expected = previous.origin + previous.sent_bytes / rt.BYTES_PER_SECOND
                    self.assertGreater(abs(second_start - expected), 1 / RATE)
                    self.assertGreater(abs(second_start - expected), rt.TIMESTAMP_TOLERANCE)

                    sent.clear()
                    following = rt._Connection("after-gap")
                    self.assertEqual(self.run_sender(client, following, send), "finish")
                    self.assertEqual(sent, [second, json.dumps({"end": True})])
                    self.assertEqual(following.origin, second_start)
                    self.assertEqual(following.sent_bytes, len(second))
                    self.assertEqual(client._pending_bytes, 0)
                    self.assertEqual(client.poll(), [])


if __name__ == "__main__":
    unittest.main()
