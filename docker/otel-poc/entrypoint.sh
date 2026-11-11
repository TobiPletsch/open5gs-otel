#!/bin/bash
# Same behaviour as the gradiant/open5gs image entrypoint: create the TUN
# device for UPF/PGW, then run the command passed as arguments.

set -eo pipefail

tun_create() {
    if ! grep "ogstun" /proc/net/dev > /dev/null; then
        echo "Creating ogstun device"
        ip tuntap add name ogstun mode tun
    fi

    ip addr del "$IPV4_TUN_ADDR" dev ogstun 2> /dev/null || true
    ip addr add "$IPV4_TUN_ADDR" dev ogstun

    sysctl -w net.ipv6.conf.all.disable_ipv6=0
    ip addr del "$IPV6_TUN_ADDR" dev ogstun 2> /dev/null || true
    ip addr add "$IPV6_TUN_ADDR" dev ogstun

    ip link set ogstun up
    sh -c "echo 1 > /proc/sys/net/ipv4/ip_forward"
    if [ "$ENABLE_NAT" = true ]; then
        iptables -t nat -A POSTROUTING -s "$IPV4_TUN_SUBNET" ! -o ogstun \
            -j MASQUERADE
    fi
}

COMMAND=$1
if [[ "$COMMAND" == *"open5gs-pgwd" ]] || [[ "$COMMAND" == *"open5gs-upfd" ]]; then
    tun_create
fi

exec "$@"
