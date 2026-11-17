# Synthetic loopback TLS fixture

This certificate and **publicly disclosed test key** belong only to the automated
home-service tests. They grant no access to an Iris installation or home device.
Never use them for deployment. The SAN contains only `127.0.0.1`; `localhost`
deliberately does not match, so the test can verify hostname checking too.

The certificate was generated on 17 November 2026 with a ten-year validity.
Before its expiry, regenerate both files with:

```sh
openssl req -x509 -newkey rsa:2048 -sha256 -nodes -days 3650 -subj '/CN=Iris synthetic loopback test only' -addext 'subjectAltName=IP:127.0.0.1' -addext 'basicConstraints=critical,CA:TRUE' -keyout tests/fixtures/tls/home-key.pem -out tests/fixtures/tls/home-cert.pem
```

No OpenSSL executable is needed to run the tests; Python's TLS library reads the
committed fixture. The fixture ships with test sources in the sdist, not as a
runtime package resource.
