"""Run the app, prepare models explicitly, or generate a notation smoke sample."""

import argparse
import json
from pathlib import Path

from .config import Settings


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Local drum transcription prototype")
    commands = parser.add_subparsers(dest="command", required=True)
    serve = commands.add_parser("serve", help="Start the local web app")
    serve.add_argument("--host", default="127.0.0.1")
    serve.add_argument("--port", type=int, default=8765)
    setup = commands.add_parser(
        "setup-models", help="Explicitly prepare/download local model files"
    )
    setup.add_argument("engine", choices=["adtof", "adt_str", "demucs"])
    setup.add_argument("--models-dir", type=Path, default=None)
    doctor = commands.add_parser("doctor", help="Report local dependencies and model readiness")
    doctor.add_argument("--models-dir", type=Path, default=None)
    demo = commands.add_parser(
        "demo", help="Export known synthetic events; does NOT measure AI quality"
    )
    demo.add_argument("--output-dir", type=Path, default=Path("data/demo"))
    args = parser.parse_args(argv)
    settings = Settings.from_env()
    models_dir = getattr(args, "models_dir", None) or settings.models_dir
    if args.command == "serve":
        import uvicorn

        from .app import create_app

        uvicorn.run(create_app(settings), host=args.host, port=args.port)
    elif args.command == "setup-models":
        if args.engine == "demucs":
            from .audio import prepare_demucs

            result = prepare_demucs(models_dir)
        else:
            from .engines import prepare_model

            result = prepare_model(args.engine, models_dir)
        print(json.dumps(result, ensure_ascii=False, indent=2))
    elif args.command == "doctor":
        from .audio import separation_status
        from .engines import get_engine_status

        print(
            json.dumps(
                {
                    "ffmpeg": settings.ffmpeg,
                    "musescore": settings.musescore,
                    "models_dir": str(models_dir),
                    "engines": get_engine_status(models_dir),
                    "separation": separation_status(models_dir),
                },
                ensure_ascii=False,
                indent=2,
            )
        )
    elif args.command == "demo":
        from .domain import ScoreOptions
        from .jobs import demo_transcription
        from .notation import export_score

        args.output_dir.mkdir(parents=True, exist_ok=True)
        transcription = demo_transcription()
        (args.output_dir / "events.json").write_text(
            transcription.model_dump_json(indent=2), encoding="utf-8"
        )
        result = export_score(
            transcription,
            ScoreOptions(title="DEMO • synthetic known events"),
            args.output_dir,
            settings.musescore,
        )
        print(
            json.dumps(
                {"demo_only": True, "output_dir": str(args.output_dir.resolve()), **result},
                ensure_ascii=False,
                indent=2,
            )
        )


if __name__ == "__main__":
    main()
