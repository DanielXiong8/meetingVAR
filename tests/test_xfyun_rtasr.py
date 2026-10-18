"""RTASR regression tests: synthetic credentials and in-process sockets only."""
from __future__ import annotations

import asyncio
import base64
import hashlib
import hmac
import inspect
import json
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest import mock
from urllib.parse import parse_qs, urlsplit

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from meetingvar.transcribe import xfyun_rtasr as rt  # noqa: E402

ENV = "APPID=synthetic_app\nAPIKey=synthetic_key\n"
SECRET = "synthetic_key"


def result(kind="0", text="完成。", *, seg=0, bg=0, ed=40, last=False):
    return {"action": "result", "code": "0", "data": json.dumps({
        "seg_id": seg, "ls": last,
        "cn": {"st": {"type": kind, "bg": bg, "ed": ed,
                       "rt": [{"ws": [{"cw": [{"w": text, "rl": "9"}]}]}]}},
    }, ensure_ascii=False)}


class FakeSocket:
    def __init__(self, *, started=True, on_audio=None, on_end=None):
        self.incoming = asyncio.Queue()
        self.sent = []
        self.closed = False
        self.on_audio = on_audio
        self.on_end = on_end
        if started:
            self.push({"action": "started", "code": "0"})

    def push(self, message):
        self.incoming.put_nowait(message if isinstance(message, (BaseException, str)) else json.dumps(message))

    async def recv(self):
        message = await self.incoming.get()
        if isinstance(message, BaseException):
            raise message
        return message

    async def send(self, payload):
        self.sent.append((time.monotonic(), payload))
        if isinstance(payload, bytes):
            callback = self.on_audio
        else:
            if json.loads(payload) != {"end": True}:
                raise AssertionError("unexpected control payload")
            callback = self.on_end
            if callback is None:
                self.push(result(ed=sum(len(p) for _, p in self.sent if isinstance(p, bytes)) / 32, last=True))
        if callback:
            response = callback(self, payload)
            if inspect.isawaitable(response):
                await response

    async def close(self):
        self.closed = True

    @property
    def audio(self):
        return [p for _, p in self.sent if isinstance(p, bytes)]


class FakeConnect:
    def __init__(self, factory=None):
        self.factory = factory or (lambda index: FakeSocket())
        self.sockets = []
        self.urls = []
        self.threads = []

    async def __call__(self, url, **kwargs):
        self.urls.append(url)
        self.threads.append(threading.get_ident())
        socket = self.factory(len(self.urls) - 1)
        self.sockets.append(socket)
        return socket


class Fixture(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="mv_cloud_test_")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.cred = self.root / "实时语音转写" / "credentials.env"
        self.cred.parent.mkdir()
        self.cred.write_text(ENV, encoding="utf-8")
        # Accidental default transport use must never reach the network.
        patcher = mock.patch.object(rt, "_ws_connect", side_effect=AssertionError("network forbidden"))
        patcher.start()
        self.addCleanup(patcher.stop)

    def client(self, connect=None):
        client = rt.XfyunRealtime(self.cred, connect=connect or FakeConnect())
        self.addCleanup(client.stop)
        client.start()
        return client

    def wait_until(self, predicate, timeout=1.5):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if predicate():
                return
            time.sleep(0.005)
        self.fail("background condition timed out")

    def collect_until(self, client, predicate, timeout=1.5):
        collected = []

        def ready():
            collected.extend(client.poll())
            return predicate(collected)

        self.wait_until(ready, timeout)
        return collected

    def assert_safe(self, events):
        messages = " ".join(e["message"] for e in events if e["type"] == "status")
        for forbidden in (SECRET, "signa=", "wss://", "synthetic_app", "untrusted-desc"):
            self.assertNotIn(forbidden, messages)


class CredentialsTests(Fixture):
    def test_directory_reads_only_selected_rtasr_file(self):
        iat = self.root / "语音听写" / "iat.env"
        iat.parent.mkdir()
        iat.write_text(ENV + "APISecret=synthetic_iat_secret\n", encoding="utf-8")
        (self.root / "unrelated.env").write_text("not a credential", encoding="utf-8")
        original = Path.read_text
        reads = []

        def guarded(path, *args, **kwargs):
            reads.append(path)
            self.assertEqual(path, self.cred)
            return original(path, *args, **kwargs)

        with mock.patch.object(Path, "read_text", guarded):
            self.assertEqual(rt.load_creds(self.root), {"APPID": "synthetic_app", "APIKey": SECRET})
        self.assertEqual(reads, [self.cred])

    def test_recognizes_filename_and_parent_names(self):
        for name in ("实时转写.env", "RTASR-account.env", "实时语音转写.env"):
            with self.subTest(name=name), tempfile.TemporaryDirectory(prefix="mv_select_") as folder:
                target = Path(folder) / name
                target.write_text(ENV, encoding="utf-8")
                self.assertEqual(rt.find_rtasr(folder), target)
        self.assertEqual(rt.find_rtasr(self.cred.parent), self.cred)

    def test_ambiguous_directory_reads_nothing(self):
        (self.root / "other-rtasr.env").write_text(ENV, encoding="utf-8")
        with mock.patch.object(Path, "read_text", side_effect=AssertionError("must select first")):
            with self.assertRaisesRegex(ValueError, "多个"):
                rt.load_creds(self.root)

    def test_iat_is_not_used_even_under_rtasr_parent(self):
        iat = self.cred.parent / "语音听写" / "service.env"
        iat.parent.mkdir()
        iat.write_text(ENV + "APISecret=synthetic_iat_secret\n", encoding="utf-8")
        self.assertEqual(rt.find_rtasr(self.root), self.cred)
        with self.assertRaisesRegex(ValueError, "IAT"):
            rt.load_creds(iat)
        neutral = self.root / "chosen.env"
        neutral.write_text(ENV + "APISecret=synthetic_iat_secret\n", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "IAT"):
            rt.load_creds(neutral)

    def test_directory_has_no_key_based_fallback(self):
        with tempfile.TemporaryDirectory(prefix="mv_unlabelled_") as folder:
            (Path(folder) / "unknown.env").write_text(ENV, encoding="utf-8")
            with mock.patch.object(Path, "read_text", side_effect=AssertionError("unselected file")):
                with self.assertRaisesRegex(ValueError, "RTASR"):
                    rt.load_creds(folder)

    def test_missing_invalid_and_unreadable_credentials_are_safe(self):
        for content in ("APPID=synthetic_app\n", "APIKey=synthetic_key\n", "APPID=\nAPIKey=x\n"):
            self.cred.write_text(content, encoding="utf-8")
            with self.assertRaises(ValueError) as error:
                rt.load_creds(self.cred)
            self.assertNotIn("synthetic", str(error.exception))
        with mock.patch.object(Path, "read_text", side_effect=OSError("signa=synthetic_key")):
            with self.assertRaises(ValueError) as error:
                rt.load_creds(self.cred)
            self.assertNotIn(SECRET, str(error.exception))
            self.assertTrue(error.exception.__suppress_context__)

    def test_explicit_file_accepts_bom_quotes_and_comment_lines(self):
        neutral = self.root / "chosen.env"
        neutral.write_text('\ufeff// demo\n# demo\nAPPID="synthetic_app"\nAPIKey=\'synthetic_key\'\n', encoding="utf-8")
        self.assertEqual(rt.load_creds(neutral), {"APPID": "synthetic_app", "APIKey": SECRET})

    def test_verified_hexdigest_signature_and_no_roles(self):
        creds = {"APPID": "synthetic_app", "APIKey": SECRET}
        with mock.patch.object(rt.time, "time", return_value=1700000000):
            parsed = urlsplit(rt.ws_url(creds))
        query = parse_qs(parsed.query)
        digest = hashlib.md5(b"synthetic_app1700000000")
        expected = base64.b64encode(hmac.new(SECRET.encode(), digest.hexdigest().encode(), hashlib.sha1).digest()).decode()
        wrong = base64.b64encode(hmac.new(SECRET.encode(), digest.digest(), hashlib.sha1).digest()).decode()
        self.assertEqual((parsed.scheme, parsed.netloc, parsed.path), ("wss", "rtasr.xfyun.cn", "/v1/ws"))
        self.assertEqual(query, {"appid": ["synthetic_app"], "ts": ["1700000000"], "signa": [expected], "lang": ["cn"]})
        self.assertNotEqual(expected, wrong)
        self.assertNotIn("roleType", query)


class StreamingTests(Fixture):
    def test_arbitrary_fragments_exact_frames_tail_and_real_pacing(self):
        connect = FakeConnect()
        client = self.client(connect)
        pcm = bytes(range(256)) * 11 + b"x" * 186  # 3002 bytes
        offset = 0
        for size in (1, 3, 77, 1000, 199, 1281, 441):
            client.feed(pcm[offset:offset + size], 100.0 + offset / 32000)
            offset += size
        self.assertEqual(offset, len(pcm))
        events = client.finish(2)
        socket = connect.sockets[0]
        self.assertEqual(list(map(len, socket.audio)), [1280, 1280, 442])
        self.assertEqual(b"".join(socket.audio), pcm)
        times = [t for t, p in socket.sent if isinstance(p, bytes)]
        # Compensating a Windows wakeup overshoot may shorten the next gap.
        # Check cumulative pacing here; virtual-clock tests pin the deadlines.
        self.assertGreaterEqual(times[-1] - times[0], 0.07)
        self.assertEqual(json.loads(socket.sent[-1][1]), {"end": True})
        self.assertTrue(socket.closed)
        self.assertEqual(connect.threads, [client._thread.ident])
        self.assertNotEqual(connect.threads[0], threading.get_ident())
        final = next(e for e in events if e["type"] == "final")
        self.assertAlmostEqual(final["start_ts"], 100)
        self.assertAlmostEqual(final["end_ts"], 100 + len(pcm) / 32000)
        self.assertEqual(final["speaker"], "spk?")
        self.assertEqual(client.poll(), [])
        self.assert_safe(events)

    def test_incomplete_frame_waits_for_next_feed(self):
        connect = FakeConnect()
        client = self.client(connect)
        client.feed(b"a" * 20, 0)
        self.collect_until(client, lambda es: any(e.get("state") == "ready" for e in es))
        time.sleep(0.03)
        self.assertEqual(connect.sockets[0].audio, [])
        client.feed(b"b" * 1260, 20 / 32000)
        client.finish(1)
        self.assertEqual(connect.sockets[0].audio, [b"a" * 20 + b"b" * 1260])

    def test_partial_overwrites_final_once_and_zero_timestamps(self):
        def audio(socket, payload):
            socket.push(result("1", "你", ed=0))
            socket.push(result("1", "你好", ed=0))

        def end(socket, payload):
            socket.push(result("0", "你好。", ed=0))
            socket.push(result("0", "重复结果", ed=0))
            socket.push(result("1", "迟到的中间结果", ed=0))
            socket.push(result("0", "再次重复", ed=0, last=True))

        client = self.client(FakeConnect(lambda i: FakeSocket(on_audio=audio, on_end=end)))
        client.feed(b"x" * 1280, 0.0)
        events = self.collect_until(client, lambda es: any(e["type"] == "partial" for e in es))
        partials = [e for e in events if e["type"] == "partial"]
        self.assertEqual([e["text"] for e in partials], ["你好"])
        events = client.finish(1)
        finals = [e for e in events if e["type"] == "final"]
        self.assertEqual(len(finals), 1)
        self.assertEqual(finals[0]["text"], "你好。")
        self.assertEqual(finals[0]["id"], partials[0]["id"])
        self.assertEqual((finals[0]["start_ts"], finals[0]["end_ts"]), (0.0, 0.0))
        self.assertFalse(any(e["type"] == "partial" for e in events))

    def test_nonzero_bg_partial_with_zero_or_missing_ed_overwrites(self):
        def without_ed(message):
            data = json.loads(message["data"])
            del data["cn"]["st"]["ed"]
            message["data"] = json.dumps(data)
            return message

        def audio(socket, payload):
            if len(socket.audio) == 1:
                socket.push(result("1", "你", bg=1200, ed=0))
                socket.push(result("1", "你好", bg="1200", ed="0"))
            else:
                socket.push(without_ed(result("1", "你好世界", bg=1200)))
                socket.push(result("1", "你好世界！", bg=1200, ed=None))

        def end(socket, payload):
            socket.push(result("0", "无效结束时间", bg=1200, ed=0))
            socket.push(without_ed(result("0", "缺少结束时间", bg=1200)))
            socket.push(result("0", "你好世界。", bg=1200, ed=1800, last=True))

        client = self.client(FakeConnect(lambda i: FakeSocket(on_audio=audio, on_end=end)))
        client.feed(b"x" * 1280, 100)
        first = self.collect_until(client, lambda es: any(e.get("text") == "你好" for e in es))
        client.feed(b"x" * 1280, 100.04)
        second = self.collect_until(client, lambda es: any(e.get("text") == "你好世界！" for e in es))
        partials = [e for e in first + second if e["type"] == "partial"]
        self.assertEqual([e["text"] for e in partials], ["你好", "你好世界！"])
        self.assertEqual(len({e["id"] for e in partials}), 1)
        self.assertTrue(all((e["start_ts"], e["end_ts"]) == (101.2, 101.2) for e in partials))
        self.assertFalse(any(e.get("state") == "error" or e["type"] == "final" for e in first + second))
        events = client.finish(1)
        finals = [e for e in events if e["type"] == "final"]
        self.assertEqual(len(finals), 1)
        self.assertEqual(finals[0]["id"], partials[0]["id"])
        self.assertEqual(finals[0]["text"], "你好世界。")
        self.assertEqual((finals[0]["start_ts"], finals[0]["end_ts"]), (101.2, 101.8))
        self.assertEqual(sum(e.get("state") == "error" for e in events), 2)

    def test_invalid_timestamps_and_schema_never_make_final(self):
        bad_offsets = [(None, 10), ("bad", 10), (0, None), (-1, 10), (20, 10),
                       (0, "nan"), (0, float("inf")), (False, 0), ({}, 10)]

        def end(socket, payload):
            for bg, ed in bad_offsets:
                socket.push(result(seg=0, bg=bg, ed=ed))
            missing = result()
            data = json.loads(missing["data"])
            del data["cn"]["st"]["ed"]
            missing["data"] = json.dumps(data)
            socket.push(missing)
            socket.push(result(seg=None))
            socket.push(result(kind="2"))
            socket.push("not json")
            socket.push({"action": "result", "data": "[]"})
            socket.push(result(seg=0, bg="0", ed="40", last=True))

        client = self.client(FakeConnect(lambda i: FakeSocket(on_end=end)))
        client.feed(b"x" * 1280, 100)
        events = client.finish(1)
        finals = [e for e in events if e["type"] == "final"]
        self.assertEqual(len(finals), 1)
        self.assertEqual((finals[0]["start_ts"], finals[0]["end_ts"]), (100, 100.04))
        self.assertEqual(sum(e.get("state") == "error" for e in events), len(bad_offsets) + 5)

    def test_invalid_last_result_is_error_not_fabricated_final(self):
        client = self.client(FakeConnect(lambda i: FakeSocket(on_end=lambda s, p: s.push(result(bg=None, last=True)))))
        client.feed(b"x" * 1280, 100)
        events = client.finish(1)
        self.assertTrue(any(e.get("state") == "error" for e in events))
        self.assertFalse(any(e["type"] == "final" for e in events))

    def test_gap_and_overlap_reconnect_preserving_absolute_mapping(self):
        for second_start in (105.0, 99.0):
            with self.subTest(second_start=second_start):
                connect = FakeConnect()
                client = self.client(connect)
                client.feed(b"a" * 640, 100)
                client.feed(b"b" * 1280, second_start)
                events = client.finish(2)
                self.assertEqual(len(connect.sockets), 2)
                self.assertEqual(connect.sockets[0].audio, [b"a" * 640])
                self.assertEqual(connect.sockets[1].audio, [b"b" * 1280])
                finals = [e for e in events if e["type"] == "final"]
                self.assertEqual([e["start_ts"] for e in finals], [100, second_start])
                self.assertEqual([e["end_ts"] for e in finals], [100.02, second_start + 0.04])
                self.assertNotEqual(finals[0]["id"], finals[1]["id"])
                self.assertTrue(any(e.get("state") == "reconnecting" for e in events))
                self.assertTrue(all(s.closed for s in connect.sockets))

    def test_disconnect_keeps_unsent_pcm_and_changes_epoch(self):
        def disconnect(socket, payload):
            loop = asyncio.get_running_loop()
            loop.call_later(0.005, socket.push, result())
            loop.call_later(0.006, socket.push, OSError("wss://example/?signa=synthetic_key"))

        connect = FakeConnect(lambda i: FakeSocket(on_audio=disconnect) if i == 0 else FakeSocket())
        with mock.patch.object(rt, "RECONNECT_DELAY", 0.01):
            client = self.client(connect)
            client.feed(b"a" * 1280 + b"b" * 1280, 100)
            events = client.finish(2)
        finals = [e for e in events if e["type"] == "final"]
        self.assertEqual([e["start_ts"] for e in finals], [100, 100.04])
        self.assertEqual(len({e["id"] for e in finals}), 2)
        self.assertEqual(connect.sockets[1].audio, [b"b" * 1280])
        self.assertTrue(any(e.get("state") == "error" for e in events))
        self.assertTrue(any(e.get("state") == "reconnecting" for e in events))
        self.assert_safe(events)

    def test_send_failure_preserves_frame_for_new_connection(self):
        def fail(socket, payload):
            raise OSError("synthetic_key signa=secret synthetic_app")

        connect = FakeConnect(lambda i: FakeSocket(on_audio=fail) if i == 0 else FakeSocket())
        with mock.patch.object(rt, "RECONNECT_DELAY", 0.01):
            client = self.client(connect)
            client.feed(b"a" * 1280, 123)
            events = client.finish(1)
        self.assertEqual(connect.sockets[1].audio, [b"a" * 1280])
        self.assertEqual(next(e for e in events if e["type"] == "final")["start_ts"], 123)
        self.assert_safe(events)

    def test_odd_final_fragment_has_explicit_padding_status(self):
        connect = FakeConnect()
        client = self.client(connect)
        client.feed(b"abc", 10)
        events = client.finish(1)
        self.assertEqual(connect.sockets[0].audio, [b"abc\x00"])
        self.assertTrue(any(e.get("state") == "error" and "字节" in e["message"] for e in events))

    def test_finish_waits_for_delayed_final(self):
        def end(socket, payload):
            asyncio.get_running_loop().call_later(0.07, socket.push, result(text="最终文本。", last=True))

        client = self.client(FakeConnect(lambda i: FakeSocket(on_end=end)))
        client.feed(b"x" * 1280, 5)
        started = time.monotonic()
        events = client.finish(1)
        self.assertGreaterEqual(time.monotonic() - started, 0.10)
        self.assertEqual(next(e["text"] for e in events if e["type"] == "final"), "最终文本。")


class SchedulingTests(Fixture):
    def run_clocked_sender(self, frame_count, overshoots, *, send_stall=None, wake_stall=None):
        """Exercise the real sender/queue with a virtual monotonic clock."""
        client = rt.XfyunRealtime(self.cred, connect=FakeConnect())
        self.addCleanup(client.stop)
        client._started = True  # run just the sender in this test's asyncio loop
        pcm = b"x" * rt.FRAME_BYTES
        send_stall = send_stall or {}
        wake_stall = wake_stall or {}
        starts, ends, sleeps, control = [], [], [], []
        epoch = 1000.0
        now = epoch

        class Clock:
            def time(self):
                return now

        async def sleep(delay):
            nonlocal now
            index = len(sleeps)
            self.assertGreaterEqual(delay, 0)
            sleeps.append(delay)
            now += delay + overshoots[index % len(overshoots)] + wake_stall.get(index, 0)

        async def send(socket, payload):
            nonlocal now
            if isinstance(payload, bytes):
                self.assertEqual(payload, pcm)
                index = len(starts)
                starts.append(now)
                now += 0.001 + send_stall.get(index, 0)  # encode/send bookkeeping
                ends.append(now)
                if index + 1 < frame_count:
                    client.feed(pcm, 100 + (index + 1) * rt.FRAME_SECONDS)
                else:
                    client._finishing = True
            else:
                self.assertEqual(json.loads(payload), {"end": True})
                control.append(now)

        async def exercise():
            client._wake = asyncio.Event()
            state = rt._Connection("clock-test")
            client.feed(pcm, 100)
            with mock.patch.object(rt.asyncio, "get_running_loop", return_value=Clock()), \
                    mock.patch.object(rt.asyncio, "sleep", new=sleep), \
                    mock.patch.object(client, "_send", new=send):
                self.assertEqual(await client._send_audio(object(), state), "finish")
            self.assertEqual(state.sent_bytes, frame_count * rt.FRAME_BYTES)
            self.assertEqual(client._pending_bytes, 0)

        asyncio.run(exercise())
        self.assertEqual(len(starts), frame_count)
        self.assertEqual(len(control), 1)
        return epoch, starts, ends, sleeps, control[0]

    def test_slow_send_allows_one_compensation_then_resumes_pacing(self):
        # Python 3.10 on Windows uses 15.625-ms GetTickCount64 for loop.time().
        # A real asyncio.sleep(0.07) cannot establish an exact 70-ms stall.
        # Exercise the real sender against a deterministic monotonic clock:
        # 70 ms stalled send + 1 ms bookkeeping, one immediately due frame,
        # then 40-ms cumulative deadlines (not another 9-ms catch-up frame).
        epoch, starts, ends, sleeps, finished = self.run_clocked_sender(
            4, (0.0,), send_stall={0: 0.07})
        self.assertAlmostEqual(ends[0] - epoch, 0.071, places=7)
        self.assertAlmostEqual(starts[1] - starts[0], 0.071, places=7)
        self.assertAlmostEqual(starts[1], ends[0], places=7)
        self.assertEqual(sleeps[1], 0)
        for previous, current in zip(starts[1:], starts[2:]):
            self.assertAlmostEqual(current - previous, rt.FRAME_SECONDS, places=7)
        for delay in sleeps[2:]:
            self.assertAlmostEqual(delay, rt.FRAME_SECONDS - 0.001, places=7)
        self.assertAlmostEqual(finished - starts[-1], rt.FRAME_SECONDS, places=7)

    def test_ten_minute_stream_does_not_accumulate_2_to_8ms_overshoot(self):
        frames = 15000  # 600 s; the old relative sleeps accrued 45-135 s of lag
        for overshoots in ((0.002,), (0.008,), (0.002, 0.004, 0.006, 0.008)):
            with self.subTest(overshoots=overshoots):
                epoch, starts, _, _, ended = self.run_clocked_sender(frames, overshoots)
                errors = [actual - (epoch + i * rt.FRAME_SECONDS) for i, actual in enumerate(starts)]
                self.assertGreaterEqual(min(errors), -0.00001)
                self.assertLessEqual(max(errors), max(overshoots) + 0.00001)
                self.assertLessEqual(abs(ended - (epoch + frames * rt.FRAME_SECONDS)), 0.00801)
                # Per-frame error is bounded; it does not turn into queue backlog.
                self.assertLessEqual(abs(errors[-1] - errors[0]), 0.00601)

    def test_long_stall_allows_one_due_frame_then_future_deadlines(self):
        for source in ("send", "wake"):
            with self.subTest(source=source):
                kwargs = {f"{source}_stall": {3: 1.3}}
                _, starts, ends, sleeps, _ = self.run_clocked_sender(12, (0.002,), **kwargs)
                self.assertLessEqual(starts[4] - ends[3], 0.00201)
                self.assertEqual(sleeps[4], 0)
                self.assertTrue(all(delay > 0.03 for delay in sleeps[5:]))
                self.assertTrue(all(b - a >= 0.0399 for a, b in zip(starts[4:], starts[5:])))


class LifecycleTests(Fixture):
    def test_start_and_feed_do_not_wait_and_overflow_rejects_whole_block(self):
        async def blocked(url, **kwargs):
            await asyncio.Event().wait()

        with mock.patch.object(rt, "MAX_PENDING_BYTES", 2560):
            before = time.monotonic()
            client = self.client(blocked)
            client.feed(b"a" * 1280, 0)
            client.feed(b"b" * 1280, 0.04)
            self.assertLess(time.monotonic() - before, 0.2)
            with self.assertRaisesRegex(BufferError, "队列已满"):
                client.feed(b"c" * 2, 0.08)
            self.assertEqual(client._pending_bytes, 2560)
            self.assertEqual(len(client._pending), 2)
            self.assertTrue(any(e.get("state") == "error" for e in client.poll()))
            client.stop()
            self.assertFalse(client._thread.is_alive())

    def test_queue_limit_includes_frame_in_flight(self):
        async def blocked(socket, payload):
            await asyncio.Event().wait()

        connect = FakeConnect(lambda i: FakeSocket(on_audio=blocked))
        with mock.patch.object(rt, "MAX_PENDING_BYTES", 1280):
            client = self.client(connect)
            client.feed(b"a" * 1280, 0)
            self.wait_until(lambda: connect.sockets and connect.sockets[0].audio)
            with self.assertRaises(BufferError):
                client.feed(b"b" * 2, 0.04)
            client.stop()
        self.assertTrue(connect.sockets[0].closed)

    def test_missing_started_times_out_without_sending_audio(self):
        connect = FakeConnect(lambda i: FakeSocket(started=False))
        with mock.patch.object(rt, "STARTED_TIMEOUT", 0.03), mock.patch.object(rt, "RECONNECT_DELAY", 0.01):
            client = self.client(connect)
            client.feed(b"a" * 1280, 1)
            events = client.finish(0.12)
            client.stop()
        self.assertTrue(any(e.get("state") == "error" and "started" in e["message"] for e in events))
        self.assertTrue(all(not s.audio and s.closed for s in connect.sockets))
        self.assertFalse(client._thread.is_alive())
        self.assert_safe(events)

    def test_open_timeout_is_visible(self):
        async def blocked(url, **kwargs):
            await asyncio.Event().wait()

        with mock.patch.object(rt, "CONNECT_TIMEOUT", 0.02), mock.patch.object(rt, "RECONNECT_DELAY", 0.01):
            client = self.client(blocked)
            events = self.collect_until(client, lambda es: any(e.get("state") == "error" for e in es))
            client.stop()
        self.assertTrue(any("建立连接超时" in e.get("message", "") for e in events))
        self.assertFalse(client._thread.is_alive())

    def test_server_error_and_connect_exception_are_redacted(self):
        def factory(index):
            socket = FakeSocket(started=False)
            socket.push({"action": "error", "code": "10110", "desc": "untrusted-desc signa=synthetic_key"})
            return socket

        client = self.client(FakeConnect(factory))
        events = self.collect_until(client, lambda es: any(e.get("state") == "error" for e in es))
        client.stop()
        self.assert_safe(events)
        self.assertTrue(any("IAT" in e.get("message", "") for e in events))

        async def fail(url, **kwargs):
            raise RuntimeError(url + " synthetic_key")

        client = self.client(fail)
        events = self.collect_until(client, lambda es: any(e.get("state") == "error" for e in es))
        client.stop()
        self.assert_safe(events)

    def test_send_timeout_is_visible_and_retry_keeps_pcm(self):
        async def blocked(socket, payload):
            await asyncio.Event().wait()

        connect = FakeConnect(lambda i: FakeSocket(on_audio=blocked) if i == 0 else FakeSocket())
        with mock.patch.object(rt, "SEND_TIMEOUT", 0.03), mock.patch.object(rt, "RECONNECT_DELAY", 0.01):
            client = self.client(connect)
            client.feed(b"a" * 1280, 0)
            events = client.finish(1)
        self.assertTrue(any("发送超时" in e.get("message", "") for e in events))
        self.assertEqual(connect.sockets[1].audio, [b"a" * 1280])

    def test_final_wait_timeout_is_visible(self):
        connect = FakeConnect(lambda i: FakeSocket(on_end=False))
        with mock.patch.object(rt, "FINAL_TIMEOUT", 0.03):
            client = self.client(connect)
            client.feed(b"a" * 1280, 0)
            events = client.finish(1)
        self.assertTrue(any("最终结果超时" in e.get("message", "") for e in events))
        self.assertTrue(connect.sockets[0].closed)
        self.assertFalse(any(e["type"] == "final" for e in events))

    def test_finish_total_timeout_is_bounded_and_stop_reclaims_thread(self):
        connect = FakeConnect(lambda i: FakeSocket(on_end=False))
        client = self.client(connect)
        client.feed(b"a" * 32000, 0)
        before = time.monotonic()
        events = client.finish(0.05)
        self.assertLess(time.monotonic() - before, 0.2)
        self.assertTrue(any("finish 超时" in e.get("message", "") for e in events))
        client.stop()
        self.assertFalse(client._thread.is_alive())
        self.assertTrue(all(s.closed for s in connect.sockets))
        self.assertEqual(client._pending_bytes, 0)

    def test_idle_timeout_preserves_subframe_for_reconnection(self):
        connect = FakeConnect()
        with mock.patch.object(rt, "AUDIO_IDLE_TIMEOUT", 0.03), mock.patch.object(rt, "RECONNECT_DELAY", 0.01):
            client = self.client(connect)
            client.feed(b"a" * 80, 200)
            events = self.collect_until(client, lambda es: any(e.get("state") == "reconnecting" for e in es))
            events.extend(client.finish(1))
        self.assertTrue(any("等待 PCM 超时" in e.get("message", "") for e in events))
        self.assertEqual(b"".join(p for s in connect.sockets for p in s.audio), b"a" * 80)
        self.assertEqual(next(e["start_ts"] for e in events if e["type"] == "final"), 200)

    def test_context_manager_connector_is_closed(self):
        sockets = []
        exited = threading.Event()

        class Manager:
            async def __aenter__(self):
                self.ws = FakeSocket()
                sockets.append(self.ws)
                return self.ws

            async def __aexit__(self, *args):
                await self.ws.close()
                exited.set()

        client = self.client(lambda url, **kwargs: Manager())
        client.feed(b"x" * 1280, 1)
        client.finish(1)
        self.assertTrue(exited.is_set())
        self.assertTrue(sockets[0].closed)

    def test_close_timeout_does_not_leak_worker(self):
        class SlowClose(FakeSocket):
            async def close(self):
                await asyncio.Event().wait()

        with mock.patch.object(rt, "CLOSE_TIMEOUT", 0.02):
            client = self.client(FakeConnect(lambda i: SlowClose()))
            client.feed(b"x" * 1280, 1)
            events = client.finish(1)
            client.stop()
        self.assertTrue(any("关闭失败或超时" in e.get("message", "") for e in events))
        self.assertFalse(client._thread.is_alive())

    def test_lifecycle_idempotency_and_input_validation(self):
        client = rt.XfyunRealtime(self.cred, connect=FakeConnect())
        self.addCleanup(client.stop)
        with self.assertRaises(RuntimeError):
            client.feed(b"x", 0)
        for timestamp in (None, True, "secret", float("nan"), float("inf"), 10**1000):
            with self.assertRaises(ValueError):
                client.feed(b"x", timestamp)
        client.start()
        thread = client._thread
        client.start()
        self.assertIs(client._thread, thread)
        with self.assertRaises(TypeError):
            client.feed("secret", 0)
        for budget in (-1, float("nan"), float("inf"), True, "secret", 10**1000):
            with self.assertRaises(ValueError):
                client.finish(budget)
        client.stop()
        client.stop()
        self.assertFalse(thread.is_alive())
        with self.assertRaises(RuntimeError):
            client.start()
        with self.assertRaises(RuntimeError):
            client.feed(b"x", 0)

    def test_stop_during_started_timeout_cleanup_still_closes_socket(self):
        class StopDuringCleanup(FakeSocket):
            async def recv(self):
                try:
                    return await super().recv()
                finally:
                    # The started timeout is already cleaning up; now finish
                    # or stop cancels the parent at exactly that boundary.
                    client._request_stop()

        connect = FakeConnect(lambda i: StopDuringCleanup(started=False))
        client = rt.XfyunRealtime(self.cred, connect=connect)
        self.addCleanup(client.stop)
        with mock.patch.object(rt, "STARTED_TIMEOUT", 0.02):
            before = time.monotonic()
            client.start()
            self.assertTrue(client._done.wait(1))
            client.stop()
        self.assertLess(time.monotonic() - before, 1)
        self.assertEqual(len(connect.sockets), 1)
        self.assertTrue(connect.sockets[0].closed)
        self.assertFalse(client._thread.is_alive())
        self.assertEqual(connect.sockets[0].audio, [])

    def test_stop_without_audio_after_ready_is_bounded(self):
        connect = FakeConnect()
        client = self.client(connect)
        self.collect_until(client, lambda es: any(e.get("state") == "ready" for e in es))
        before = time.monotonic()
        client.stop()
        self.assertLess(time.monotonic() - before, 0.5)
        self.assertFalse(client._thread.is_alive())
        self.assertTrue(connect.sockets[0].closed)
        self.assertEqual(connect.sockets[0].audio, [])
        self.assertTrue(any(e.get("state") == "stopped" for e in client.poll()))

    def test_finish_without_audio_has_bounded_total_timeout(self):
        connect = FakeConnect(lambda i: FakeSocket(on_end=False))
        client = self.client(connect)
        self.collect_until(client, lambda es: any(e.get("state") == "ready" for e in es))
        before = time.monotonic()
        events = client.finish(0.05)
        self.assertLess(time.monotonic() - before, 0.2)
        self.assertTrue(any("finish 超时" in e.get("message", "") for e in events))
        self.assertEqual([json.loads(payload) for _, payload in connect.sockets[0].sent], [{"end": True}])
        client.stop()
        self.assertFalse(client._thread.is_alive())
        self.assertTrue(connect.sockets[0].closed)

    def test_finish_before_start_opens_no_socket(self):
        connect = FakeConnect()
        client = rt.XfyunRealtime(self.cred, connect=connect)
        self.assertTrue(any(e.get("state") == "stopped" for e in client.finish()))
        self.assertEqual(connect.urls, [])
        self.assertEqual(client.finish(), [])


if __name__ == "__main__":
    unittest.main()
