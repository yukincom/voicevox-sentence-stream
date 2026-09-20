"""Unit tests use mocks instead of native tools and models."""

import array
import contextlib
import io
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import Mock, patch
import wave

from voicevox_sentence_stream import _whisper_vad as vad
from voicevox_sentence_stream import transcription as stt


def write_wav(path, samples, rate=16000):
    with wave.open(str(path), "wb") as output:
        output.setparams((1, 2, rate, 0, "NONE", "not compressed"))
        output.writeframes(samples)


class TranscriptionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="stt tests ")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.source = self.root / "source recording.webm"
        self.source.write_bytes(b"original source audio")
        self.model = self.root / "whisper model.bin"
        self.vad_model = self.root / "silero model.bin"
        self.library = self.root / "whisper library"
        for path in (self.model, self.vad_model, self.library):
            path.touch()
        self.options = dict(model=self.model, vad_model=self.vad_model, vad_library=self.library,
                            whisper_cli="./whisper-cli", ffmpeg="./ffmpeg", language="ja")
        self.samples = array.array("h", range(16000)).tobytes()

    def recognize(self, ranges, texts=(), failure=None, missing_output=False):
        calls, pieces, directories = [], [], []

        def run(command, **kwargs):
            calls.append(command)
            self.assertTrue(kwargs["check"])
            self.assertIs(kwargs["stdout"], subprocess.DEVNULL)
            self.assertNotIn("shell", kwargs)
            if command[0] == "./ffmpeg":
                self.assertIn("-nostdin", command)
                self.assertIn(str(self.source), command)
                directories.append(Path(command[-1]).parent)
                if failure == "ffmpeg":
                    raise subprocess.CalledProcessError(7, command)
                write_wav(command[-1], self.samples)
                return
            self.assertEqual(command[0], "./whisper-cli")
            self.assertEqual(command[command.index("-m") + 1], str(self.model))
            self.assertEqual(command[command.index("--language") + 1], "ja")
            self.assertEqual(command[command.index("--max-context") + 1], "0")
            self.assertNotIn("--vad", command)
            with wave.open(command[command.index("-f") + 1], "rb") as source:
                pieces.append(source.readframes(source.getnframes()))
            if failure == len(pieces) - 1:
                raise subprocess.CalledProcessError(7, command)
            if not missing_output:
                Path(command[command.index("-of") + 1] + ".txt").write_text(
                    texts[len(pieces) - 1], encoding="utf-8"
                )

        try:
            with patch.object(stt, "speech_ranges", return_value=ranges) as detect, \
                 patch.object(stt.subprocess, "run", side_effect=run):
                result = stt.transcribe_audio(self.source, **self.options)
                self.assertIsNone(detect.call_args.kwargs["threshold"])
                self.assertIsNone(detect.call_args.kwargs["pad_ms"])
                return result, calls, pieces
        finally:
            for directory in directories:
                self.assertFalse(directory.exists(), "temporary audio was left behind")
            self.assertEqual(self.source.read_bytes(), b"original source audio")

    def test_order_exact_audio_and_real_repetitions_are_preserved(self):
        text, calls, pieces = self.recognize(
            [(100, 3000), (5000, 12000)], ["大事なことです。", "大事なことです。"]
        )
        self.assertEqual(text, "大事なことです。\n大事なことです。")
        self.assertEqual(pieces, [self.samples[200:6000], self.samples[10000:24000]])
        self.assertEqual(len(calls), 3)

    def test_no_speech_skips_whisper(self):
        text, calls, pieces = self.recognize([])
        self.assertEqual(text, "")
        self.assertEqual(len(calls), 1)
        self.assertEqual(pieces, [])

    def test_short_speech_is_not_filtered_by_text(self):
        text, _, _ = self.recognize([(0, 2000), (4000, 6000)], ["ごめん", "うん"])
        self.assertEqual(text, "ごめん\nうん")

    def test_whisper_failure_is_not_a_partial_success(self):
        with self.assertRaises(subprocess.CalledProcessError):
            self.recognize([(0, 2000), (4000, 6000)], ["first"], failure=1)

    def test_conversion_failure_cleans_up(self):
        with self.assertRaises(subprocess.CalledProcessError):
            self.recognize([], failure="ffmpeg")

    def test_missing_whisper_output_is_not_silence(self):
        with self.assertRaises(FileNotFoundError):
            self.recognize([(0, 2000)], missing_output=True)

    def test_missing_model_fails_before_running_tools(self):
        self.model.unlink()
        with patch.object(stt.subprocess, "run") as run, self.assertRaises(FileNotFoundError):
            stt.transcribe_audio(self.source, **self.options)
        run.assert_not_called()

    def test_explicit_relative_executable_and_path_objects(self):
        self.assertEqual(stt._executable("./tools/whisper-cli"), "./tools/whisper-cli")
        self.assertEqual(stt._executable("whisper-cli"), "whisper-cli")
        self.assertEqual(stt._executable(Path("whisper-cli")), str(Path("whisper-cli").resolve()))

    def cli(self, text="", error=None):
        output = self.root / "result directory"
        with patch.object(stt, "transcribe_audio", return_value=text, side_effect=error) as transcribe, \
             contextlib.redirect_stdout(io.StringIO()) as stdout, \
             contextlib.redirect_stderr(io.StringIO()):
            code = stt.main(["--input", str(self.source), "--outdir", str(output),
                             "--model", str(self.model), "--vad-model", str(self.vad_model),
                             "--vad-library", str(self.library), "--language", "ja",
                             "--vad-min-silence-ms", "500", "--vad-pad-ms", "200"])
        return code, output / "transcript.txt", stdout.getvalue(), transcribe

    def test_cli_publishes_only_complete_text_and_passes_configuration(self):
        code, output, stdout, transcribe = self.cli("最初\n最後")
        self.assertEqual(code, 0)
        self.assertEqual(output.read_text(encoding="utf-8"), "最初\n最後")
        self.assertEqual(stdout, "")
        self.assertEqual(transcribe.call_args.kwargs["vad_min_silence_ms"], 500)
        self.assertEqual(transcribe.call_args.kwargs["vad_pad_ms"], 200)

    def test_cli_successful_silence_is_an_empty_file(self):
        code, output, stdout, _ = self.cli()
        self.assertEqual(code, 0)
        self.assertEqual(output.read_bytes(), b"")
        self.assertEqual(stdout, "")

    def test_cli_failure_creates_no_transcript(self):
        code, output, stdout, _ = self.cli(error=RuntimeError("recognition failed"))
        self.assertEqual(code, 1)
        self.assertFalse(output.exists())
        self.assertEqual(stdout, "")

    def test_cli_does_not_overwrite_or_append_to_an_existing_transcript(self):
        _, output, _, _ = self.cli("keep this")
        code, _, _, transcribe = self.cli("replacement")
        self.assertEqual(code, 1)
        transcribe.assert_not_called()
        self.assertEqual(output.read_text(encoding="utf-8"), "keep this")

    def test_cli_removes_its_own_partial_file_on_write_failure(self):
        real_open = Path.open

        def fail_write(path, *args, **kwargs):
            handle = real_open(path, *args, **kwargs)
            if path.name != "transcript.txt" or not args or args[0] != "x":
                return handle
            wrapper = Mock(wraps=handle)
            wrapper.__enter__ = Mock(return_value=wrapper)
            wrapper.__exit__ = Mock(side_effect=lambda *unused: handle.close())
            wrapper.write.side_effect = OSError("disk full")
            return wrapper

        with patch.object(Path, "open", fail_write):
            code, output, stdout, _ = self.cli("complete transcript")
        self.assertEqual(code, 1)
        self.assertFalse(output.exists())
        self.assertEqual(stdout, "")

    def test_cli_does_not_delete_a_competing_writers_file(self):
        output = self.root / "result directory" / "transcript.txt"

        def concurrent_writer(*args, **kwargs):
            output.parent.mkdir()
            output.write_text("other writer", encoding="utf-8")
            return "our text"

        code, _, _, _ = self.cli(error=concurrent_writer)
        self.assertEqual(code, 1)
        self.assertEqual(output.read_text(encoding="utf-8"), "other writer")


class VadTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.wav = Path(self.temp.name) / "input.wav"
        write_wav(self.wav, bytes(32000))
        self.lib = Mock()
        self.lib.whisper_vad_default_context_params.return_value = vad.ContextParams(4, True, 0)
        self.lib.whisper_vad_default_params.return_value = vad.VadParams(.5, 250, 100, 1e30, 30, .1)
        self.lib.whisper_vad_init_from_file_with_params.return_value = 1
        self.lib.whisper_vad_segments_from_samples.return_value = 2

    def detect(self, intervals, **options):
        self.lib.whisper_vad_segments_n_segments.return_value = len(intervals)
        self.lib.whisper_vad_segments_get_segment_t0.side_effect = [start for start, _ in intervals]
        self.lib.whisper_vad_segments_get_segment_t1.side_effect = [end for _, end in intervals]
        with patch.object(vad, "_load_library", return_value=self.lib):
            return vad.speech_ranges(self.wav, Path("library"), Path("model"), **options)

    def test_units_final_padding_and_explicit_options(self):
        self.assertEqual(self.detect([(1, 30), (60, 102)], min_silence_ms=500, pad_ms=200),
                         [(160, 4800), (9600, 16000)])
        params = self.lib.whisper_vad_segments_from_samples.call_args.args[1]
        self.assertEqual(params.min_silence_duration_ms, 500)
        self.assertEqual(params.speech_pad_ms, 200)
        self.lib.whisper_vad_free_segments.assert_called_once_with(2)
        self.lib.whisper_vad_free.assert_called_once_with(1)

    def test_unspecified_settings_keep_native_defaults(self):
        self.detect([])
        params = self.lib.whisper_vad_segments_from_samples.call_args.args[1]
        self.assertEqual((params.threshold, params.min_speech_duration_ms,
                          params.min_silence_duration_ms, params.speech_pad_ms), (.5, 250, 100, 30))
        context = self.lib.whisper_vad_init_from_file_with_params.call_args.args[1]
        self.assertTrue(context.use_gpu)

    def test_no_speech_is_empty_and_releases_native_handles(self):
        self.assertEqual(self.detect([]), [])
        self.lib.whisper_vad_free_segments.assert_called_once_with(2)
        self.lib.whisper_vad_free.assert_called_once_with(1)

    def test_invalid_intervals_fail_and_release_native_handles(self):
        for intervals in [[(0, 0)], [(0, 50), (40, 60)], [(-1, 10)], [(200, 300)], [(0, float("nan"))]]:
            with self.subTest(intervals=intervals):
                self.lib.reset_mock()
                with self.assertRaises(RuntimeError):
                    self.detect(intervals)
                self.lib.whisper_vad_free_segments.assert_called_once_with(2)
                self.lib.whisper_vad_free.assert_called_once_with(1)

    def test_detection_failure_is_not_silence(self):
        self.lib.whisper_vad_segments_from_samples.return_value = None
        with self.assertRaises(RuntimeError):
            self.detect([])
        self.lib.whisper_vad_free_segments.assert_not_called()
        self.lib.whisper_vad_free.assert_called_once_with(1)

    def test_context_failure_does_not_free_a_null_handle(self):
        self.lib.whisper_vad_init_from_file_with_params.return_value = None
        with self.assertRaises(RuntimeError):
            self.detect([])
        self.lib.whisper_vad_free.assert_not_called()

    def test_unknown_abi_fails_before_native_struct_calls(self):
        self.lib.whisper_version.return_value = b"99.0"
        with patch.object(vad.c, "CDLL", return_value=self.lib), self.assertRaises(RuntimeError):
            vad._load_library(Path("library"))
        self.lib.whisper_vad_default_context_params.assert_not_called()

    def test_invalid_options_fail_before_loading_library(self):
        for options in ({"threshold": -1}, {"threshold": float("nan")}, {"threshold": 2},
                        {"min_speech_ms": 0}, {"min_silence_ms": -1}, {"pad_ms": -1}):
            with self.subTest(options=options), patch.object(vad, "_load_library") as load, \
                 self.assertRaises(ValueError):
                vad.speech_ranges(self.wav, Path("library"), Path("model"), **options)
            load.assert_not_called()

    def test_empty_wav_needs_no_native_calls(self):
        write_wav(self.wav, b"")
        with patch.object(vad, "_load_library") as load:
            self.assertEqual(vad.speech_ranges(self.wav, Path("library"), Path("model")), [])
        load.assert_not_called()

    def test_wrong_format_and_truncated_wav_are_rejected(self):
        write_wav(self.wav, bytes(32000), rate=24000)
        with self.assertRaises(ValueError):
            self.detect([])
        write_wav(self.wav, bytes(32000))
        self.wav.write_bytes(self.wav.read_bytes()[:-2])
        with self.assertRaisesRegex(ValueError, "Truncated"):
            self.detect([])


if __name__ == "__main__":
    unittest.main()
