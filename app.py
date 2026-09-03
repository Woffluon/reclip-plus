"""ReClip Plus - Application Entrypoint.

Provides the WSGI callable 'app' for Gunicorn / production servers
and runs the local development server when executed directly.
"""

import os

from reclip.app import app
from reclip.downloader import parse_ytdlp_json
from reclip.jobs import Job, JobManager, job_manager
from reclip.utils import DOWNLOAD_DIR

__all__ = [
    "app",
    "job_manager",
    "Job",
    "JobManager",
    "parse_ytdlp_json",
    "DOWNLOAD_DIR",
]

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8899))
    host = os.environ.get("HOST", "0.0.0.0")
    try:
        app.run(host=host, port=port)
    except KeyboardInterrupt:
        pass

