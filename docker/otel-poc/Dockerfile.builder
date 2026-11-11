# Open5GS OTel PoC - build environment
#
# Ubuntu 24.04 + the upstream Open5GS build dependencies
# (docker/ubuntu/latest/base/Dockerfile) + opentelemetry-cpp, which is not
# packaged on 24.04. opentelemetry-cpp is built as static PIC libraries so it
# links into libogsotel.so and the runtime image only needs libprotobuf/libcurl.
#
#   sudo docker build -f docker/otel-poc/Dockerfile.builder \
#       -t open5gs-otel-builder:latest docker/otel-poc

FROM ubuntu:24.04

ARG OTEL_CPP_VERSION=v1.16.1
ARG JOBS=6

RUN apt-get update && \
    DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
        python3-pip python3-setuptools python3-wheel \
        ninja-build build-essential flex bison git cmake meson \
        libsctp-dev libgnutls28-dev libgcrypt-dev libssl-dev \
        libmongoc-dev libbson-dev libyaml-dev libmicrohttpd-dev \
        libcurl4-gnutls-dev libnghttp2-dev libtins-dev libtalloc-dev \
        libc-ares-dev libidn-dev iproute2 ca-certificates netbase pkg-config \
        libprotobuf-dev protobuf-compiler nlohmann-json3-dev && \
    apt-get clean && rm -rf /var/lib/apt/lists/*

RUN git clone --depth 1 --branch ${OTEL_CPP_VERSION} \
        https://github.com/open-telemetry/opentelemetry-cpp.git /tmp/otel-cpp && \
    cmake -S /tmp/otel-cpp -B /tmp/otel-cpp/build -G Ninja \
        -DCMAKE_BUILD_TYPE=Release \
        -DCMAKE_CXX_STANDARD=17 \
        -DCMAKE_POSITION_INDEPENDENT_CODE=ON \
        -DBUILD_SHARED_LIBS=OFF \
        -DWITH_OTLP_HTTP=ON \
        -DWITH_OTLP_GRPC=OFF \
        -DWITH_ABSEIL=OFF \
        -DWITH_EXAMPLES=OFF \
        -DWITH_BENCHMARK=OFF \
        -DBUILD_TESTING=OFF \
        -DOPENTELEMETRY_INSTALL=ON \
        -DCMAKE_INSTALL_PREFIX=/usr/local && \
    cmake --build /tmp/otel-cpp/build -j ${JOBS} && \
    cmake --install /tmp/otel-cpp/build && \
    rm -rf /tmp/otel-cpp

WORKDIR /src
