"""Read stdin incrementally and save each generated sentence immediately."""

import argparse
from pathlib import Path
import sys

from . import VoicevoxClient, iter_audio


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default="http://127.0.0.1:50021")
    parser.add_argument("--speaker", type=int, default=3)
    parser.add_argument("--output-dir", type=Path, default=Path("audio"))
    parser.add_argument("--max-chars", type=int, default=180)
    args = parser.parse_args()
    args.output_dir.mkdir(parents=True, exist_ok=True)
    client = VoicevoxClient(args.base_url, args.speaker)
    # read(1), unlike read() or line iteration, does not wait for EOF/newline.
    deltas = iter(lambda: sys.stdin.read(1), "")
    for index, audio in enumerate(iter_audio(deltas, client, max_chars=args.max_chars), 1):
        path = args.output_dir / f"{index:04d}.wav"
        with path.open("xb") as output:
            output.write(audio.wav)
        print(path, flush=True)


if __name__ == "__main__":
    main()
