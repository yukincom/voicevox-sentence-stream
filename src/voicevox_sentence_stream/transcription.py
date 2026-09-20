"""Optional local STT: recognize Silero speech intervals separately, in order.

External prerequisites: FFmpeg, whisper.cpp 1.8.3 CLI/shared library, a Whisper
model and a compatible Silero model. Nothing is downloaded or started on import.
"""

import argparse
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import wave

from ._whisper_vad import speech_ranges


def _existing_file(path: str | Path) -> Path:
    resolved = Path(path).expanduser().resolve()
    if not resolved.is_file():
        raise FileNotFoundError(f"Required file does not exist: {resolved}")
    return resolved


def _executable(value: str | Path) -> str:
    # Preserve './tool' (Path would strip './' and accidentally search PATH).
    return str(value.expanduser().resolve()) if isinstance(value, Path) else os.path.expanduser(value)


def transcribe_audio(input_path: str | Path, *, model: str | Path, vad_model: str | Path,
                     vad_library: str | Path, whisper_cli: str | Path = "whisper-cli",
                     ffmpeg: str | Path = "ffmpeg", language: str = "auto",
                     vad_threshold: float | None = None, vad_min_speech_ms: int | None = None,
                     vad_min_silence_ms: int | None = None, vad_pad_ms: int | None = None) -> str:
    """Return all interval transcripts joined with newlines, or empty for no speech.

    The caller supplies an audio file. Actual spoken repetitions are preserved.
    VAD settings left unspecified use whisper.cpp's own defaults. Failed
    conversion, detection or recognition raises; no partial result is returned.
    Temporary audio is removed on success and failure. The input is never edited.
    """
    input_path = _existing_file(input_path)
    model = _existing_file(model)
    vad_model = _existing_file(vad_model)
    vad_library = _existing_file(vad_library)
    with tempfile.TemporaryDirectory(prefix="whisper-segmented-") as temporary:
        directory = Path(temporary)
        wav = directory / "input.wav"
        subprocess.run([
            _executable(ffmpeg), "-nostdin", "-hide_banner", "-loglevel", "error",
            "-i", str(input_path), "-vn", "-ac", "1", "-ar", "16000", "-c:a", "pcm_s16le", str(wav),
        ], check=True, stdout=subprocess.DEVNULL)
        ranges = speech_ranges(wav, vad_library, vad_model, threshold=vad_threshold,
                               min_speech_ms=vad_min_speech_ms, min_silence_ms=vad_min_silence_ms,
                               pad_ms=vad_pad_ms)
        texts = []
        with wave.open(str(wav), "rb") as source:
            for index, (start, end) in enumerate(ranges):
                segment = directory / f"{index}.wav"
                output = directory / f"{index}"
                source.setpos(start)
                samples = source.readframes(end - start)
                if len(samples) != (end - start) * 2:
                    raise ValueError("Truncated speech interval")
                with wave.open(str(segment), "wb") as target:
                    target.setparams(source.getparams())
                    target.writeframes(samples)
                subprocess.run([
                    _executable(whisper_cli), "-m", str(model), "-f", str(segment),
                    "--language", language, "-otxt", "-of", str(output), "--max-context", "0",
                ], check=True, stdout=subprocess.DEVNULL)
                texts.append(output.with_suffix(".txt").read_text(encoding="utf-8").strip())
        return "\n".join(text for text in texts if text)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", required=True, type=Path)
    parser.add_argument("--outdir", required=True, type=Path, help="Write transcript.txt here; never overwrite it")
    parser.add_argument("--model", required=True, type=Path, help="Whisper GGML model")
    parser.add_argument("--vad-model", required=True, type=Path, help="Silero GGML model")
    parser.add_argument("--vad-library", required=True, type=Path, help="whisper.cpp 1.8.3 shared library")
    parser.add_argument("--whisper-cli", default="whisper-cli")
    parser.add_argument("--ffmpeg", default="ffmpeg")
    parser.add_argument("--language", default="auto")
    parser.add_argument("--vad-threshold", type=float)
    parser.add_argument("--vad-min-speech-ms", type=int)
    parser.add_argument("--vad-min-silence-ms", type=int)
    parser.add_argument("--vad-pad-ms", type=int)
    args = parser.parse_args(argv)
    output = args.outdir.expanduser().resolve() / "transcript.txt"
    try:
        if output.exists():
            raise FileExistsError(f"Transcript already exists: {output}")
        text = transcribe_audio(args.input, model=args.model, vad_model=args.vad_model,
                                vad_library=args.vad_library, whisper_cli=args.whisper_cli,
                                ffmpeg=args.ffmpeg, language=args.language,
                                vad_threshold=args.vad_threshold, vad_min_speech_ms=args.vad_min_speech_ms,
                                vad_min_silence_ms=args.vad_min_silence_ms, vad_pad_ms=args.vad_pad_ms)
        output.parent.mkdir(parents=True, exist_ok=True)
        transcript = output.open("x", encoding="utf-8")
        try:
            with transcript:
                transcript.write(text)
        except BaseException:
            # Only remove the file we successfully created, never a pre-existing
            # or competing writer's file when exclusive open itself failed.
            output.unlink(missing_ok=True)
            raise
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError, wave.Error) as error:
        print(f"whisper-segmented-stt: {error}", file=sys.stderr)
        return 1
    # Keep stdout empty: command providers may treat it as fallback transcript
    # text when the output file is empty (the successful no-speech case).
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
