# 2. Compiling and building the image

All builds run on **`pub-k8s-w3`** (`k8s-worker3`) in the fork `~/open5gs-otel`.
This is deliberate: the resulting image is imported straight into that node's
containerd, so no registry is needed. It is also the reason why all Open5GS
pods are pinned to that node (see [03](03-deployment.md)).

## 2.1 Why a builder container

opentelemetry-cpp is not packaged for Ubuntu 24.04, and the build should not
install anything on the Kubernetes worker itself. `docker/otel-poc/Dockerfile.builder`
therefore provides a self-contained build environment:

* Ubuntu 24.04 plus the upstream Open5GS build dependencies, the same list as
  `docker/ubuntu/latest/base/Dockerfile`, plus `libprotobuf-dev` and
  `protobuf-compiler`.
* **opentelemetry-cpp v1.16.1** with these CMake options:
  `-DBUILD_SHARED_LIBS=OFF -DCMAKE_POSITION_INDEPENDENT_CODE=ON -DWITH_OTLP_HTTP=ON -DWITH_OTLP_GRPC=OFF -DWITH_ABSEIL=OFF`,
  installed to `/usr/local`.
  * Static and PIC means the SDK is linked *into* `libogsotel.so`. The runtime
    image then only needs `libprotobuf` and `libcurl`, which are already
    Open5GS dependencies.
  * Without gRPC and abseil, the build stays small and fast.

Build the builder once, or after changing `OTEL_CPP_VERSION`:

```sh
cd ~/open5gs-otel
sudo docker build -f docker/otel-poc/Dockerfile.builder \
     -t open5gs-otel-builder:latest docker/otel-poc
```

## 2.2 Meson configuration

The OTel code is enabled with a Meson option:

```sh
meson setup build-otel --prefix=/opt/open5gs -Dotel=true    # -Dotel_prefix=/usr/local is the default
ninja -C build-otel
meson install -C build-otel
```

* `-Dotel=false` (the default) builds `ogs-otel-void.c`. That is an unchanged
  Open5GS build with no new dependency.
* `-Dotel=true` builds `ogs-otel-sdk.cpp` and links these opentelemetry-cpp
  archives explicitly:
  `exporter_otlp_http`, `exporter_otlp_http_client`, `otlp_recordable`,
  `http_client_curl`, `proto`, `trace`, `logs`, `metrics`, `resources`,
  `common`, `version`.
  * opentelemetry-cpp's CMake package provides no pkg-config file for the OTLP
    exporter, which is why the archives are listed by hand.
  * `logs` and `metrics` are needed even though only traces are used, because
    the OTLP recordable library references their symbols. Leaving them out
    produced undefined-symbol link errors.
* `--prefix=/opt/open5gs` matches the layout of the `gradiant/open5gs` image
  that the Helm charts expect.

## 2.3 One-step build: `build-image.sh`

```sh
cd ~/open5gs-otel
git pull                                  # branch otel-poc
docker/otel-poc/build-image.sh --import   # JOBS=8 by default, e.g. JOBS=4 to limit
```

What the script does:

1. **Compile and install inside the builder.** It runs
   `open5gs-otel-builder:latest` with the source tree mounted at `/src`, then
   runs `meson setup` (first time only), `ninja` and `meson install`.
   `/opt/open5gs` is packed into `build-otel/opt-open5gs.tar`. The build
   directory is `build-otel/` in the source tree, so later builds are
   incremental.
2. **Build the runtime image.** It runs `docker build` with
   `docker/otel-poc/Dockerfile` on a temporary context that contains the tar
   and `entrypoint.sh`.
   * The tag is `open5gs-otel:<short git sha>`.
   * The suffix `-dirty` is added if there are uncommitted changes. Commit
     before building an image you want to deploy.
   * The Dockerfile fails if `ldd` reports any missing library.
3. **`--import`** runs `docker save | ctr -n k8s.io images import -`. That makes
   the image visible to Kubernetes on this node as
   `docker.io/library/open5gs-otel:<sha>`.

Verify the result:

```sh
sudo ctr -n k8s.io images ls | grep open5gs-otel
sudo docker run --rm open5gs-otel:<sha> open5gs-amfd -v   # version banner
```

## 2.4 Runtime image

`docker/otel-poc/Dockerfile` is a drop-in for `docker.io/gradiant/open5gs`:

| Property | Value |
|---|---|
| Base | `ubuntu:24.04` with only the runtime libraries |
| Binaries | `/opt/open5gs/bin` (on `PATH`), configs under `/opt/open5gs/etc/open5gs` |
| User | `open5gs`, UID/GID 999, the same as Gradiant, so the chart's `securityContext` works |
| Entrypoint | `/entrypoint.sh`: creates the `ogstun` TUN device when the command is `open5gs-upfd`/`open5gs-pgwd` (NAT on 10.45.0.0/16), then `exec "$@"` |

The same image is used for **all** NFs, not only the AMF and SMF. Mixing the
2.8-based AMF/SMF with Gradiant's 2.7.5 NRF/UDM/PCF/UPF would put two different
SBI/PFCP code bases into one core.

## 2.5 Making a code change end to end

```sh
# on pub-k8s-w3
cd ~/open5gs-otel
# edit, then:
git commit -am "otel: ..."
git push origin otel-poc          # never push to upstream open5gs
docker/otel-poc/build-image.sh --import
```

Then update the image tag in the deployment repo and restart the core, as
described in [03-deployment.md](03-deployment.md#36-rolling-out-a-new-image).

> **Network note:** the lab uplink has a path-MTU black hole (1460 bytes).
> Large uploads from lab hosts, such as `git push` of big commits, can stall.
> See [06](06-limitations-and-troubleshooting.md#64-troubleshooting).
