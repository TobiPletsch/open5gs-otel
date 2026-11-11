#!/bin/bash
# Builds the Open5GS OTel PoC runtime image from this source tree.
#
#   docker/otel-poc/build-image.sh [--import]
#
# 1. compiles and installs Open5GS (-Dotel=true, prefix /opt/open5gs) inside
#    the open5gs-otel-builder image (build it first, see Dockerfile.builder)
# 2. builds open5gs-otel:<git-sha> from the installed tree
# 3. --import: loads the image into the local containerd (k8s.io namespace),
#    so pods on this node can use it with imagePullPolicy: IfNotPresent
#
# Needs docker (via sudo). JOBS limits build parallelism (default 8).

set -euo pipefail

SRC=$(cd "$(dirname "$0")/../.." && pwd)
BUILD_DIR=build-otel
JOBS=${JOBS:-8}
BUILDER=open5gs-otel-builder:latest

TAG=$(git -C "$SRC" rev-parse --short HEAD)
if ! git -C "$SRC" diff --quiet HEAD -- . ':!build-*'; then
    TAG="$TAG-dirty"
fi
IMAGE=open5gs-otel:$TAG

echo ">> compiling and installing ($BUILD_DIR, -j$JOBS)"
sudo docker run --rm -v "$SRC":/src -w /src \
    -e HOST_UID="$(id -u)" -e HOST_GID="$(id -g)" "$BUILDER" sh -ec "
        if [ ! -d $BUILD_DIR ]; then
            meson setup $BUILD_DIR --prefix=/opt/open5gs -Dotel=true
        fi
        ninja -C $BUILD_DIR -j$JOBS
        meson install -C $BUILD_DIR --quiet
        tar -C / -cf $BUILD_DIR/opt-open5gs.tar opt/open5gs
        chown -R \$HOST_UID:\$HOST_GID $BUILD_DIR"

CTX=$(mktemp -d)
trap 'rm -rf "$CTX"' EXIT
cp "$SRC/$BUILD_DIR/opt-open5gs.tar" "$SRC/docker/otel-poc/entrypoint.sh" "$CTX/"

echo ">> building $IMAGE"
sudo docker build -f "$SRC/docker/otel-poc/Dockerfile" -t "$IMAGE" "$CTX"

if [ "${1:-}" = "--import" ]; then
    echo ">> importing $IMAGE into containerd (k8s.io)"
    sudo docker save "$IMAGE" | sudo ctr -n k8s.io images import -
fi

echo ">> done: $IMAGE"
