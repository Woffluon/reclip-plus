#!/bin/sh
set -e

# ReClip Plus startup entrypoint.
# By default, yt-dlp is shipped with the image for instant zero-network startup.
# If you want to check for and install yt-dlp updates on every container start,
# set RECLIP_UPDATE_ON_STARTUP=1 in your environment.
if [ "$RECLIP_UPDATE_ON_STARTUP" = "1" ] || [ "$RECLIP_UPDATE_ON_STARTUP" = "true" ]; then
    if [ -z "$RECLIP_NO_UPDATE" ]; then
        echo "Checking for yt-dlp updates on startup..."
        pip install --user --no-cache-dir -q -U yt-dlp || \
            echo "  (could not update yt-dlp — continuing with installed image version)"
    fi
fi

exec "$@"
