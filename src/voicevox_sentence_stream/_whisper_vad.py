"""Small ctypes adapter for the whisper.cpp 1.8.3 Silero VAD API.

ABI reference: https://github.com/ggml-org/whisper.cpp/blob/v1.8.3/include/whisper.h
No model, native library, or microphone is opened at import time.
"""

import array
import ctypes as c
import math
import os
from pathlib import Path
import sys
import wave


class ContextParams(c.Structure):
    _fields_ = [("n_threads", c.c_int), ("use_gpu", c.c_bool), ("gpu_device", c.c_int)]


class VadParams(c.Structure):
    _fields_ = [
        ("threshold", c.c_float), ("min_speech_duration_ms", c.c_int),
        ("min_silence_duration_ms", c.c_int), ("max_speech_duration_s", c.c_float),
        ("speech_pad_ms", c.c_int), ("samples_overlap", c.c_float),
    ]


def _load_library(path: Path):
    lib = c.CDLL(str(path))
    # Check the version before configuring or calling any by-value structures.
    lib.whisper_version.argtypes = []
    lib.whisper_version.restype = c.c_char_p
    if lib.whisper_version() != b"1.8.3":
        raise RuntimeError("Segmented STT requires the verified whisper.cpp 1.8.3 shared library")
    signatures = {
        "whisper_vad_default_context_params": ([], ContextParams),
        "whisper_vad_default_params": ([], VadParams),
        "whisper_vad_init_from_file_with_params": ([c.c_char_p, ContextParams], c.c_void_p),
        "whisper_vad_segments_from_samples": ([c.c_void_p, VadParams, c.POINTER(c.c_float), c.c_int], c.c_void_p),
        "whisper_vad_segments_n_segments": ([c.c_void_p], c.c_int),
        "whisper_vad_segments_get_segment_t0": ([c.c_void_p, c.c_int], c.c_float),
        "whisper_vad_segments_get_segment_t1": ([c.c_void_p, c.c_int], c.c_float),
        "whisper_vad_free_segments": ([c.c_void_p], None),
        "whisper_vad_free": ([c.c_void_p], None),
    }
    for name, (arguments, result) in signatures.items():
        function = getattr(lib, name)
        function.argtypes = arguments
        function.restype = result
    return lib


def speech_ranges(wav: Path, library: Path, model: Path, *, threshold: float | None = None,
                  min_speech_ms: int | None = None, min_silence_ms: int | None = None,
                  pad_ms: int | None = None) -> list[tuple[int, int]]:
    """Padded, non-overlapping half-open frame ranges in the original PCM WAV."""
    if threshold is not None and not 0 <= threshold <= 1:
        raise ValueError("VAD threshold must be between 0 and 1")
    if min_speech_ms is not None and min_speech_ms <= 0:
        raise ValueError("VAD minimum speech duration must be positive")
    if any(value is not None and value < 0 for value in (min_silence_ms, pad_ms)):
        raise ValueError("VAD silence duration and padding must be non-negative")
    with wave.open(str(wav), "rb") as source:
        if (source.getnchannels(), source.getsampwidth(), source.getframerate()) != (1, 2, 16000):
            raise ValueError("VAD requires mono 16-bit PCM at 16000 Hz")
        frames = source.getnframes()
        raw = source.readframes(frames)
        if len(raw) != frames * 2:
            raise ValueError("Truncated PCM WAV")
        pcm = array.array("h", raw)
    if sys.byteorder != "little":
        pcm.byteswap()
    if not pcm:
        return []
    floats = array.array("f", (sample / 32768.0 for sample in pcm))
    samples = (c.c_float * len(floats)).from_buffer(floats)
    lib = _load_library(library)
    context = lib.whisper_vad_init_from_file_with_params(
        os.fsencode(model), lib.whisper_vad_default_context_params()
    )
    if not context:
        raise RuntimeError("Could not initialize Silero VAD")
    segments = None
    try:
        params = lib.whisper_vad_default_params()
        for field, value in (("threshold", threshold), ("min_speech_duration_ms", min_speech_ms),
                             ("min_silence_duration_ms", min_silence_ms), ("speech_pad_ms", pad_ms)):
            if value is not None:
                setattr(params, field, value)
        segments = lib.whisper_vad_segments_from_samples(context, params, samples, len(samples))
        if not segments:
            raise RuntimeError("Silero VAD failed to analyze audio")
        count = lib.whisper_vad_segments_n_segments(segments)
        if count < 0:
            raise RuntimeError("Silero VAD returned an invalid interval count")
        ranges = []
        previous_end = 0
        for index in range(count):
            # whisper.cpp timestamps are centiseconds: 160 frames per cs.
            start = lib.whisper_vad_segments_get_segment_t0(segments, index)
            end = lib.whisper_vad_segments_get_segment_t1(segments, index)
            if not math.isfinite(start) or not math.isfinite(end):
                raise RuntimeError("Silero VAD returned invalid timestamps")
            start_frame, end_frame = round(start * 160), min(round(end * 160), len(samples))
            if start_frame < previous_end or end_frame <= start_frame:
                raise RuntimeError("Silero VAD returned invalid or overlapping intervals")
            ranges.append((start_frame, end_frame))
            previous_end = end_frame
        return ranges
    finally:
        if segments:
            lib.whisper_vad_free_segments(segments)
        lib.whisper_vad_free(context)
