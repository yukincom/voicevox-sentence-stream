# Optional segmented Whisper transcription

This is a separate speech-to-text (STT) utility, not a VOICEVOX feature. It can
be used by Hermes or any application that supplies an audio file. The existing
sentence-by-sentence text-to-speech API and CLI remain unchanged.

## What it fixes

Some long-recording / pause-heavy Whisper workflows repeat, omit or reorder text
when detected speech is concatenated into one long recognition pass. This utility
normalizes the audio, obtains Silero speech intervals, and recognizes each interval
independently with `--max-context 0`, then joins results in their original order.
Silence is not sent to Whisper. Actual repeated speech is kept; no text is
deduplicated, summarized or corrected by an LLM.

This is not a guarantee of perfect recognition or universal noise removal. VAD
can miss quiet speech, and a long uninterrupted utterance may remain one interval.
Each interval loads the Whisper model in a fresh process, so many intervals may
increase total time. Audio is processed as a complete recording, not live streaming.

## Prerequisites

Install this repository as described in the [README](../README.md). No additional
Python packages are required. Separately install and provide:

- [FFmpeg](https://ffmpeg.org/) (`ffmpeg` on PATH, or an explicit executable path).
- [whisper.cpp v1.8.3](https://github.com/ggml-org/whisper.cpp/tree/v1.8.3): its
  `whisper-cli` executable and shared library from the same build.
- A Whisper GGML model suitable for your language.
- A compatible Silero GGML VAD model; see the upstream
  [VAD instructions](https://github.com/ggml-org/whisper.cpp/tree/v1.8.3#voice-activity-detection-vad).

The ctypes adapter is checked against the
[v1.8.3 C header](https://github.com/ggml-org/whisper.cpp/blob/v1.8.3/include/whisper.h)
and rejects other shared-library versions. This avoids guessing the layout of
native structures. Make sure the shared library and its dependencies are loadable
by your Python architecture. Shared-library extensions typically differ by OS
(`.dylib`, `.so`, `.dll`); supply the actual path from your installation.
Native integration has been exercised on macOS; Linux/Windows native execution
is not yet verified. No models or binaries are bundled or automatically downloaded.
Their respective licenses and model terms apply separately.

## Command line

Replace the placeholder model/library paths with your installed files:

```sh
whisper-segmented-stt \
  --input recording.wav \
  --outdir transcription-output \
  --model /path/to/whisper-model.bin \
  --vad-model /path/to/silero-model.bin \
  --vad-library /path/to/libwhisper.dylib \
  --language ja
```

You can also run `python -m voicevox_sentence_stream.transcription` with the same
arguments. Use `--whisper-cli /path/to/whisper-cli` and `--ffmpeg /path/to/ffmpeg`
when the tools are not on PATH. Quote paths containing spaces. Language defaults
to `auto`; explicitly select the language for short intervals if appropriate.

The result is UTF-8 `transcript.txt` in `--outdir`; stdout is kept empty so a
command-provider host cannot mistake a result path for recognized speech.
No speech is a successful **empty file**, not an error. A conversion, VAD or
recognition failure exits nonzero without publishing a partial transcript. An
existing `transcript.txt` is never overwritten; use a fresh output directory for
each recording. The source audio is unchanged, and temporary audio/segment files
are removed on success and failure. This utility never opens a microphone, sends
a chat message, stores personal voice-analysis state or changes key bindings.

VAD options are optional; omitted values use whisper.cpp's native defaults:

```sh
# Add these options to the command above if this tuning suits your recordings:
--vad-threshold 0.5 --vad-min-speech-ms 250 --vad-min-silence-ms 500 --vad-pad-ms 200
```

Those explicit settings were used for the pause-heavy Japanese regression case;
they are not imposed as new defaults. Tune them against your own microphone and
keep genuinely spoken short phrases in your evaluation.

## Python API

```python
from voicevox_sentence_stream.transcription import transcribe_audio

text = transcribe_audio(
    "recording.wav",
    model="/path/to/whisper-model.bin",
    vad_model="/path/to/silero-model.bin",
    vad_library="/path/to/libwhisper.dylib",
    language="ja",
)
# Append to your editable draft, or pass to another consumer yourself.
print(text)
```

The function returns text (or `""` for no speech), raises on failure, and does not
write a final transcript file. It does not impose a recording-duration limit.
VAD holds the normalized recording in memory; callers manage recording length,
timeouts and cancellation for their application.

## Hermes command-provider example

Use a Hermes version that accepts successful empty command transcripts as no
speech; the [community preview](hermes.md) contains this fix. Installing this
Python package alone does not update an older Hermes installation. Merge the STT
keys into your existing configuration, keeping all other settings:

```yaml
stt:
  provider: segmented-whisper
  providers:
    segmented-whisper:
      type: command
      format: txt
      language: ja
      command: >-
        /path/to/python -m voicevox_sentence_stream.transcription
        --input {input_path} --outdir {output_dir} --language {language}
        --whisper-cli /path/to/whisper-cli
        --ffmpeg /path/to/ffmpeg
        --model /path/to/whisper-model.bin
        --vad-model /path/to/silero-model.bin
        --vad-library /path/to/libwhisper.dylib
```

Use the Python environment where this package is installed. All `/path/to/`
entries are placeholders, not user-specific defaults. Hermes substitutes the
three `{...}` placeholders; retain them. Check the provider's timeout against
your longest recording and number of intervals. This shared STT provider serves
both dictation and voice conversation; it does not add a dictation-only LLM
correction stage or change the application's recording controls.

## 日本語

間を空けた長めの口述で起きる繰り返し・抜けへの対策を、独立したコマンドとして
使える形にしたものです。無音を除いた発話をまとめ直して一括認識するのではなく、
発話区間を元の順番で個別に認識します。本当に二度言った言葉は二度のまま残します。

Python以外にFFmpeg、whisper.cpp 1.8.3、Whisperモデル、Sileroモデルが必要です。
パスはすべて利用者が指定できます。個人用の分析処理・設定ファイル・キー対応は
含みません。読み上げ機能だけを使う場合、これらSTT用の追加準備は不要です。
