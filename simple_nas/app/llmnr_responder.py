#!/usr/bin/env python3
"""Lightweight LLMNR responder for Windows 10/11 name resolution.

Windows 11 resolves local hostnames via LLMNR (Link-Local Multicast Name
Resolution, RFC 4795) on UDP port 5355.  This tiny daemon listens on the
LLMNR multicast group (224.0.0.252) and answers queries that match the
configured hostname so that \\SIMPLENAS (or whatever nas_name is set to)
resolves to this host's IP address — no client-side configuration needed.
"""

import socket
import struct
import sys
import os
import signal

LLMNR_PORT  = 5355
LLMNR_GROUP = "224.0.0.252"


def get_local_ip():
    """Determine the local IP address used for the default route."""
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return "0.0.0.0"


def parse_llmnr_name(data, offset):
    """Decode the DNS-style name from an LLMNR query packet."""
    labels = []
    while offset < len(data):
        length = data[offset]
        if length == 0:
            offset += 1
            break
        offset += 1
        labels.append(data[offset:offset + length].decode("ascii", errors="ignore"))
        offset += length
    return ".".join(labels), offset


def build_response(query_data, local_ip, ttl=30):
    """Build an LLMNR response packet for the given query."""
    # Transaction ID (first 2 bytes)
    tid = query_data[:2]

    # Flags: response (0x8000), no error
    flags = struct.pack("!H", 0x8000)

    # Counts: 1 question, 1 answer, 0 authority, 0 additional
    counts = struct.pack("!HHHH", 1, 1, 0, 0)

    # Question section (copy from query — starts at byte 12)
    # Find end of question section
    offset = 12
    _, offset = parse_llmnr_name(query_data, offset)
    offset += 4  # QTYPE (2) + QCLASS (2)
    question = query_data[12:offset]

    # Answer section: same name + type A + class IN + TTL + RDLENGTH(4) + IP
    answer = query_data[12:offset - 4]  # name + (we'll add type/class)
    answer += struct.pack("!HH", 1, 1)  # TYPE A, CLASS IN
    answer += struct.pack("!I", ttl)     # TTL
    answer += struct.pack("!H", 4)       # RDLENGTH
    answer += socket.inet_aton(local_ip) # RDATA (IPv4 address)

    return tid + flags + counts + question + answer


def main():
    hostname = sys.argv[1] if len(sys.argv) > 1 else os.environ.get("NAS_NAME", "SimpleNAS")
    hostname_upper = hostname.upper()
    local_ip = get_local_ip()

    print(f"[LLMNR] Responder started: '{hostname}' → {local_ip}", flush=True)

    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)

    try:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEPORT, 1)
    except AttributeError:
        pass

    sock.bind(("", LLMNR_PORT))

    # Join LLMNR multicast group
    mreq = struct.pack("4s4s", socket.inet_aton(LLMNR_GROUP), socket.inet_aton("0.0.0.0"))
    sock.setsockopt(socket.IPPROTO_IP, socket.IP_ADD_MEMBERSHIP, mreq)

    # Graceful shutdown
    def _shutdown(sig, frame):
        print("[LLMNR] Shutting down", flush=True)
        sock.close()
        sys.exit(0)

    signal.signal(signal.SIGTERM, _shutdown)
    signal.signal(signal.SIGINT, _shutdown)

    while True:
        try:
            data, addr = sock.recvfrom(1024)

            # Minimum LLMNR packet: 12-byte header + at least 1 byte name
            if len(data) < 13:
                continue

            # Parse header
            flags = struct.unpack("!H", data[2:4])[0]
            qr    = (flags >> 15) & 1
            qdcount = struct.unpack("!H", data[4:6])[0]

            # Only process queries (QR=0) with exactly 1 question
            if qr != 0 or qdcount != 1:
                continue

            # Parse query name
            qname, offset = parse_llmnr_name(data, 12)

            if offset + 4 > len(data):
                continue

            qtype, qclass = struct.unpack("!HH", data[offset:offset + 4])

            # Only respond to A record queries (type 1) for our hostname
            if qtype == 1 and qname.upper() == hostname_upper:
                response = build_response(data, local_ip)
                sock.sendto(response, addr)

        except Exception:
            pass


if __name__ == "__main__":
    main()
