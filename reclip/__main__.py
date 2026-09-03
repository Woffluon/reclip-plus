"""CLI entrypoint for running ReClip Plus as a module (`python -m reclip`)."""

import os
import sys

from reclip.app import app


def main() -> None:
    port = int(os.environ.get("PORT", 8899))
    host = os.environ.get("HOST", "0.0.0.0")

    # Simple CLI argument parsing: -p/--port <port>, -h/--host <host>
    args = sys.argv[1:]
    i = 0
    while i < len(args):
        arg = args[i]
        if arg in ("-p", "--port") and i + 1 < len(args):
            try:
                port = int(args[i + 1])
            except ValueError:
                pass
            i += 2
        elif arg in ("-h", "--host") and i + 1 < len(args):
            host = args[i + 1]
            i += 2
        else:
            i += 1

    try:
        app.run(host=host, port=port)
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
