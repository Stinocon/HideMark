# Synthetic test data

The fixtures in this directory and the built-in sample in `scripts/bench-suggest.py` are invented
test vectors. They are not copied from operator maps, customer documents, or the local runtime.

Fixtures that must be detected use synthetic `.local` names. `.local` is a special-use mDNS namespace, not a globally delegated domain ([IANA registry](https://www.iana.org/assignments/special-use-domain-names)); the tests exercise these strings in memory and do not resolve them. Reserved `.example.test` values remain in cases where the expected behavior is to leave a documentation example unchanged. The deliberate public-data exception is `catalogs/free-mail-domains.txt`; local server tests use fixed loopback addresses.

Some values remain realistic-shaped because the tests need to exercise the recognizer and its
validators:

- RFC 1918 IPv4 addresses are private, non-globally-unique values used to test IP detection and
  redaction. Documentation-range IPv4 addresses are used where the example does not need to be
  detected. The routed IPv6 example uses `2001:db8::/32`, reserved for documentation.
- `8.8.8.8`, `8.8.4.4`, `1.1.1.1`, `1.0.0.1`, `9.9.9.9`, and `255.255.255.255` test the explicit
  safe-infrastructure exceptions in `anon.py`; these are public resolver or broadcast values, not
  credentials or customer endpoints.
- The Italian IBAN `IT60X0542811101000000123456` is retained as a valid checksum and spacing test
  vector. SWIFT publishes it as an Italian format example in its [IBAN registry](https://www.swift.com/ru/swift-resource/9606/download); no account holder is implied.
- The codice fiscale fixture `XXXXXX00A00Z000S`, partita IVA fixture `00000000000`, and plate
  fixture `ZZ000ZZ` are synthetic shape/checksum cases. The codice fiscale validator checks format
  and checksum, not issuance against a government registry; these strings do not assert real
  identities.
- `AKIAIOSFODNN7EXAMPLE` is AWS's published example access-key ID and is used to test detection of
  that shape. An access-key ID alone is not an authentication credential; AWS documents it as an
  example [here](https://docs.aws.amazon.com/us_en/IAM/latest/UserGuide/security-creds-programmatic-access.html).
  The JWT, `sk-`-shaped strings, password-like sentinels such as `synthetic-postgrespassword-sentinel-2026`,
  the backup-test passphrase, and `SYNTHETIC-BACKUP-MARKER-2026` are constructed values used only
  to exercise detection and encryption tests.
- `Persona Test`, `Persona Due`, and similar names are explicit placeholders. Company-like names
  such as Contoso, Acme, and Northwind are conventional fictional examples. Real Italian city names,
  generic street addresses such as `Via Roma 12`, and product/vendor names appear where needed to
  test the public dictionaries and Italian address recognition; the public catalogs are reference
  data, not private customer data.
- Phone-shaped strings are fabricated test inputs for format and redaction behavior, including the
  intentionally malformed-looking `+1 000 000 0000`, `+44 00 0000 0000`, `+39 00 0000000`, and
  `3000000000` sentinels. The checks validate string shape, not carrier assignment; no subscriber is
  implied.
