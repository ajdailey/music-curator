# Run environment for Music Curator.
#
# Supplies Python and the virtualenv tooling, so a host with neither can still
# validate, test, generate and synchronize. The same scripts run inside as out.
#
# Nothing from the working tree is baked in - compose.yaml mounts it at run
# time - so editing knowledge files or source never invalidates this image.
#
# Python 3.12 matches the interpreter the project is developed against.
# pyproject.toml declares 3.11 as the floor.

FROM python:3.12-slim-bookworm

# One toolchain root, world-writable, so the container can run as the calling
# user's uid and still write the virtualenv and the pip cache. compose.yaml
# backs both with named volumes; they are the expensive part of a first run.
ENV HOME=/opt/music-curator \
    MUSIC_CURATOR_VENV=/opt/music-curator/venv \
    PIP_CACHE_DIR=/opt/music-curator/.cache/pip \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PYTHONDONTWRITEBYTECODE=1

# Created here rather than left to the container engine: a named volume
# inherits the ownership and mode of the path it covers, and one the engine
# invents belongs to root, which a container running as the caller's uid could
# not write.
RUN mkdir -p /opt/music-curator/venv /opt/music-curator/.cache/pip \
 && chmod -R a+rwX /opt/music-curator

WORKDIR /work
