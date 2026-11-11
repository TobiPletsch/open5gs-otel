# Open5GS OTel PoC - runtime image
#
# Drop-in replacement for docker.io/gradiant/open5gs (as used by the
# Gradiant Helm charts): binaries in /opt/open5gs/bin (on PATH), configs
# under /opt/open5gs/etc/open5gs, user open5gs (uid 999), /entrypoint.sh
# runs the command given as container args.
#
# The context is a directory containing opt-open5gs.tar (an installed
# /opt/open5gs tree) and entrypoint.sh - see build-image.sh.

FROM ubuntu:24.04

RUN apt-get update && \
    DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
        libsctp1 libgnutls30t64 libgcrypt20 libssl3t64 \
        libmongoc-1.0-0t64 libbson-1.0-0t64 libyaml-0-2 \
        libmicrohttpd12t64 libcurl3t64-gnutls libnghttp2-14 \
        libtins4.5 libtalloc2 libcares2 libidn12 libprotobuf32t64 \
        iproute2 iptables iputils-ping procps ca-certificates netbase && \
    apt-get clean && rm -rf /var/lib/apt/lists/*

ADD opt-open5gs.tar /
COPY entrypoint.sh /entrypoint.sh

RUN chmod 755 /entrypoint.sh && \
    (userdel -r ubuntu 2>/dev/null || true) && \
    groupadd -g 999 open5gs && \
    useradd -u 999 -g 999 -d /opt/open5gs -s /bin/bash open5gs && \
    mkdir -p /opt/open5gs/var/log/open5gs && \
    chown -R open5gs:open5gs /opt/open5gs/var && \
    ldd /opt/open5gs/bin/open5gs-* | grep "not found" && exit 1 || true

ENV PATH=/opt/open5gs/bin:$PATH \
    APP_ROOT=/opt/open5gs \
    IPV4_TUN_SUBNET=10.45.0.0/16 \
    IPV4_TUN_ADDR=10.45.0.1/16 \
    IPV6_TUN_ADDR=cafe::1/64 \
    ENABLE_NAT=true

USER open5gs
WORKDIR /opt/open5gs
ENTRYPOINT ["/entrypoint.sh"]
CMD ["/bin/bash"]
