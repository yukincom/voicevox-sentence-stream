import io
import json
import subprocess
import sys
import tempfile
import threading
import unittest
import wave
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.error import HTTPError
from urllib.parse import parse_qs, urlsplit

from voicevox_sentence_stream import SentenceSplitter, VoicevoxClient, iter_audio


def wav_bytes(rate=24000, channels=1, width=2):
    output = io.BytesIO()
    with wave.open(output, "wb") as audio:
        audio.setnchannels(channels)
        audio.setsampwidth(width)
        audio.setframerate(rate)
        audio.writeframes(b"\0" * 40 * width * channels)
    return output.getvalue()


class SplitterTests(unittest.TestCase):
    def test_short_japanese_is_immediate(self):
        splitter = SentenceSplitter()
        self.assertEqual(list(splitter.feed("う")), [])
        self.assertEqual(list(splitter.feed("ん。次！本当？")), ["うん。", "次！", "本当？"])
        self.assertEqual(list(splitter.flush()), [])

    def test_all_text_preserved_and_chunk_size_bounded(self):
        text = " うん。" + "長" * 1001 + "終わり！ 3.14 is pi. Next?\n末尾  "
        for step in (1, 2, 7, 2000):
            with self.subTest(step=step):
                splitter = SentenceSplitter(32)
                output = []
                for offset in range(0, len(text), step):
                    output.extend(splitter.feed(text[offset:offset + step]))
                output.extend(splitter.flush())
                self.assertEqual("".join(output), text)
                self.assertTrue(all(len(part) <= 32 for part in output))
                self.assertEqual(list(splitter.flush()), [])

    def test_latin_boundary_across_deltas_and_decimal(self):
        splitter = SentenceSplitter()
        self.assertEqual(list(splitter.feed("3.14 works.")), [])
        self.assertEqual(list(splitter.feed(" Yes!\n")), ["3.14 works. ", "Yes!\n"])

    def test_invalid_size(self):
        with self.assertRaises(ValueError):
            SentenceSplitter(0)


class HTTPTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_POST(self):
                url = urlsplit(self.path)
                body = self.rfile.read(int(self.headers.get("Content-Length", "0")))
                self.server.calls.append((url.path, parse_qs(url.query), body))
                callback = self.server.callback
                if callback:
                    callback(url.path)
                status = self.server.status
                data = (json.dumps({"outputSamplingRate": 48000, "outputStereo": True}).encode()
                        if url.path.endswith("/audio_query") else self.server.audio)
                self.send_response(status)
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        cls.base_url = f"http://127.0.0.1:{cls.server.server_port}/engine"

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join()

    def setUp(self):
        self.server.calls = []
        self.server.status = 200
        self.server.callback = None
        self.server.audio = wav_bytes()
        self.client = VoicevoxClient(self.base_url + "/", speaker=8)

    def test_real_http_contract_and_format(self):
        audio = self.client.synthesize("声 & 日本語？")
        query, synthesis = self.server.calls
        self.assertEqual(query[0], "/engine/audio_query")
        self.assertEqual(query[1], {"speaker": ["8"], "text": ["声 & 日本語？"]})
        self.assertEqual(synthesis[0], "/engine/synthesis")
        self.assertEqual(synthesis[1], {"speaker": ["8"]})
        payload = json.loads(synthesis[2])
        self.assertEqual(payload["outputSamplingRate"], 24000)
        self.assertIs(payload["outputStereo"], False)
        self.assertEqual((audio.sample_rate, audio.channels, audio.sample_width), (24000, 1, 2))
        self.assertEqual(audio.wav, self.server.audio)
        self.assertEqual(audio.pcm, b"\0" * 80)

    def test_early_audio_and_lazy_input(self):
        consumed = []

        def deltas():
            consumed.append(1)
            yield "うん。"
            consumed.append(2)
            yield "後半。"

        stream = iter_audio(deltas(), self.client)
        self.assertEqual(next(stream).text, "うん。")
        self.assertEqual(consumed, [1])
        self.assertEqual(len(self.server.calls), 2)
        self.assertEqual([chunk.text for chunk in stream], ["後半。"])

    def test_cancel_pending_sentences_and_input(self):
        cancel = threading.Event()
        stream = iter_audio(["うん。待機。", "消える。"], self.client, cancel=cancel)
        self.assertEqual(next(stream).text, "うん。")
        cancel.set()
        self.assertEqual(list(stream), [])
        self.assertEqual(len(self.server.calls), 2)

        def no_input():
            raise AssertionError("cancelled iterator must not pull input")
            yield ""

        self.assertEqual(list(iter_audio(no_input(), self.client, cancel=cancel)), [])

    def test_cancel_between_http_requests(self):
        cancel = threading.Event()
        self.server.callback = lambda path: cancel.set()
        self.assertIsNone(self.client.synthesize("止めて。", cancel=cancel))
        self.assertEqual(len(self.server.calls), 1)

    def test_cancel_during_synthesis_discards_audio(self):
        cancel = threading.Event()
        self.server.callback = lambda path: cancel.set() if path.endswith("/synthesis") else None
        self.assertEqual(list(iter_audio(["止めて。次。"], self.client, cancel=cancel)), [])
        self.assertEqual(len(self.server.calls), 2)

    def test_http_errors_propagate_without_retry(self):
        self.server.status = 503
        with self.assertRaises(HTTPError) as caught:
            list(iter_audio(["うん。"], self.client))
        caught.exception.close()
        self.assertEqual(len(self.server.calls), 1)

    def test_format_mismatch_rejected(self):
        for args in ((48000, 1, 2), (24000, 2, 2), (24000, 1, 1)):
            with self.subTest(args=args):
                self.server.audio = wav_bytes(*args)
                with self.assertRaisesRegex(ValueError, "PCM format"):
                    self.client.synthesize("うん。")

    def test_truncated_audio_rejected(self):
        self.server.audio = wav_bytes()[:-2]
        with self.assertRaisesRegex(ValueError, "truncated"):
            self.client.synthesize("うん。")

    def test_response_cap(self):
        client = VoicevoxClient(self.base_url, max_response_bytes=64)
        with self.assertRaisesRegex(ValueError, "max_response_bytes"):
            client.synthesize("うん。")

    def test_cli_writes_before_stdin_eof(self):
        ready = threading.Event()
        paths = []
        with tempfile.TemporaryDirectory() as directory:
            process = subprocess.Popen([sys.executable, "-m", "voicevox_sentence_stream",
                                        "--base-url", self.base_url, "--output-dir", directory],
                                       stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                       stderr=subprocess.PIPE, text=True, encoding="utf-8")

            def read_output():
                paths.append(process.stdout.readline().strip())
                ready.set()

            reader = threading.Thread(target=read_output, daemon=True)
            reader.start()
            try:
                process.stdin.write("うん。")
                process.stdin.flush()
                self.assertTrue(ready.wait(5), "CLI waited for newline or EOF")
                self.assertTrue(Path(paths[0]).is_file())
                self.assertIsNone(process.poll())
                process.stdin.close()
                self.assertEqual(process.wait(timeout=5), 0)
            finally:
                if process.poll() is None:
                    process.kill()
                    process.wait()
                reader.join(timeout=5)
                process.stdout.close()
                process.stderr.close()


if __name__ == "__main__":
    unittest.main()
