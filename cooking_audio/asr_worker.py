"""Run in the dedicated Python 3.11 MLX environment, never the Flask interpreter."""
import argparse
import json
from pathlib import Path

if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('audio')
    parser.add_argument('output')
    parser.add_argument('--model', required=True)
    args = parser.parse_args()
    import mlx_whisper
    result = mlx_whisper.transcribe(args.audio, path_or_hf_repo=args.model,
                                    language='ko', word_timestamps=True, verbose=False,
                                    condition_on_previous_text=False)
    Path(args.output).write_text(json.dumps(result, ensure_ascii=False), encoding='utf-8')
