#!/usr/bin/env python3
"""
Generate a self-signed TLS certificate for running ScamSentinel over HTTPS
on your local network.

WHY YOU NEED THIS:
Browsers only allow microphone access (getUserMedia) on "secure contexts" --
that's https://, OR http://localhost. Plain http://<your-lan-ip>:8000, which
is what a second laptop has to use to reach you, is NOT secure, so the
browser silently blocks the microphone there. This is almost always why a
call "connects" but never transcribes/analyzes anything.

USAGE:
    python3 generate_cert.py [YOUR_LAN_IP]

Example:
    python3 generate_cert.py 192.168.1.100

If you omit the IP, the cert will still work for https://localhost:8000
but NOT for another laptop connecting to you by IP.

This writes cert.pem and key.pem into the current directory. Then start the
server with:

    uvicorn server:app --host 0.0.0.0 --port 8000 --ssl-keyfile key.pem --ssl-certfile cert.pem

Both laptops then browse to https://YOUR_LAN_IP:8000 -- the browser will
warn "Your connection is not private" because the cert is self-signed and
not from a trusted CA. That's expected for a local dev cert; click
"Advanced" -> "Proceed to ... (unsafe)" on BOTH laptops. This is a one-time
click per laptop per cert.
"""
import ipaddress
import sys
from datetime import datetime, timedelta, timezone

try:
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    from cryptography.x509.oid import NameOID
except ImportError:
    print("Missing dependency. Install it with:\n    pip install cryptography")
    sys.exit(1)


def main():
    extra_ip = sys.argv[1] if len(sys.argv) > 1 else None

    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)

    subject = issuer = x509.Name([
        x509.NameAttribute(NameOID.COMMON_NAME, "scamsentinel.local"),
    ])

    san_entries = [
        x509.DNSName("localhost"),
        x509.IPAddress(ipaddress.ip_address("127.0.0.1")),
    ]
    if extra_ip:
        try:
            san_entries.append(x509.IPAddress(ipaddress.ip_address(extra_ip)))
            print(f"Including {extra_ip} in the certificate.")
        except ValueError:
            print(f"'{extra_ip}' doesn't look like a valid IP address, skipping it.")

    cert = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(issuer)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(datetime.now(timezone.utc) - timedelta(days=1))
        .not_valid_after(datetime.now(timezone.utc) + timedelta(days=825))
        .add_extension(x509.SubjectAlternativeName(san_entries), critical=False)
        .sign(key, hashes.SHA256())
    )

    with open("key.pem", "wb") as f:
        f.write(key.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.TraditionalOpenSSL,
            encryption_algorithm=serialization.NoEncryption(),
        ))

    with open("cert.pem", "wb") as f:
        f.write(cert.public_bytes(serialization.Encoding.PEM))

    print("\nWrote cert.pem and key.pem.")
    print("\nStart the server with:")
    print("    uvicorn server:app --host 0.0.0.0 --port 8000 --ssl-keyfile key.pem --ssl-certfile cert.pem")
    if extra_ip:
        print(f"\nThen on both laptops open: https://{extra_ip}:8000")
    else:
        print("\nThen open: https://localhost:8000")
        print("(Re-run this script with your LAN IP as an argument to also allow a second laptop to connect.)")


if __name__ == "__main__":
    main()
