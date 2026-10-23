"""corpus.py — a labelled, synthetic corpus for measuring the engine's RECALL.

`scripts/fp-sweep.py` measures the other direction (false positives over a corpus known to be
clean). This file is the oracle for the direction that matters more for an anonymizer: of the
values a document DECLARES sensitive, how many does `anon.detect()` actually cover?

Every document here is synthetic — no real name, client or address. Each one declares:

  * `entities`   the dictionary text (TIPO|valore lines) the document assumes, exactly as
                 `anon.load_entities` reads it. Recall is meaningless without it: the engine can
                 only find what the operator declared.
  * `catalogs`   catalog names loaded from `catalogs/` (opt-in, off by default).
  * `patterns`   pattern families (`identity`/`network`/`legal`), or None for all.
  * `must_find`  (value, type) pairs that MUST be covered. A value with no covered occurrence is a
                 LEAK, and the gate fails.
  * `must_not`   strings that must NEVER be covered. A covered one is a false positive, and the
                 gate fails.
  * `declared_fp` (value, type, why) accepted false positives, reported and not failed.
  * `known_miss`  (value, why) values the engine is known not to find — the hole is stated, not
                 hidden.

The corpus is deliberately adversarial: the `must_not` entries are ordinary prose that RESEMBLES
a pattern (`in via del tutto eccezionale, 3 volte`), and the `known_miss` entries are the declared
limits (a contextual reference, a name absent from the dictionary).
"""

from __future__ import annotations

# A value the engine must cover. Tuples keep the file readable and diffable.
Document = dict

DOCUMENTS: list[Document] = [
    {
        "name": "verbale-sopralluogo",
        "entities": (
            "@type AZIENDA\n"
            "Contoso S.r.l.\n"
            "@type PERSONA\n"
            "PERSONA|Persona Test|p.test@contoso.local\n"
            "Persona Due\n"
        ),
        "catalogs": ["it-cities", "vendors", "products"],
        "patterns": None,
        "text": (
            "Oggetto: verbale di sopralluogo presso la sede di Ancona.\n"
            "\n"
            "Cliente Contoso S.r.l., referente Persona Test (p.test@contoso.local, +39 000 0000000).\n"
            "Sede in Via Roma 12, 60100 (AN).\n"
            "Firewall FortiGate, hostname fw-ancona.contoso.local, indirizzo 10.42.7.19.\n"
            "Server Dell PowerEdge R740 su 10.42.7.20, seconda NIC su 2001:db8::42.\n"
            "Portale https://vpn.contoso.local accessibile dal browser.\n"
            "Switch Cisco Catalyst 2960, seriale non rilevante.\n"
            "Verbale redatto da Persona Due.\n"
        ),
        "must_find": [
            ("Contoso S.r.l.", "AZIENDA"),
            ("Persona Test", "PERSONA"),
            ("p.test@contoso.local", "EMAIL"),  # the EMAIL pattern outranks the dictionary on this span
            ("+39 000 0000000", "TEL"),
            ("Via Roma 12", "INDIRIZZO"),
            ("Ancona", "CITTÀ"),
            ("FortiGate", "PRODOTTO"),
            ("fw-ancona.contoso.local", "HOST"),
            ("10.42.7.19", "IP"),
            ("10.42.7.20", "IP"),
            ("2001:db8::42", "IP"),
            ("https://vpn.contoso.local", "URL"),
            ("Dell", "FORNITORE"),
            ("PowerEdge", "PRODOTTO"),
            ("Cisco", "FORNITORE"),
            ("Catalyst", "PRODOTTO"),
            ("Persona Due", "PERSONA"),
        ],
        "must_not": ["60100", "R740", "2960"],
    },
    {
        "name": "contratto-fornitura",
        "entities": (
            "@type AZIENDA\n"
            "Northwind S.p.A.\n"
        ),
        "catalogs": [],
        "patterns": None,
        "text": (
            "Contratto di fornitura tra Northwind S.p.A. e il committente sotto indicato.\n"
            "Partita IVA 00000000000, codice fiscale del referente XXXXXX00A00Z000S.\n"
            "Pagamento sul conto IT60 X054 2811 1010 0000 0123 456 entro trenta giorni.\n"
            "Sede operativa: Corso Buenos Aires 12.\n"
            "Automezzo aziendale targato ZZ000ZZ.\n"
            "IBAN malformato con doppio spazio: IT60 X054  2811 1010 0000 0123 456.\n"
        ),
        "must_find": [
            ("Northwind S.p.A.", "AZIENDA"),
            ("00000000000", "PARTITAIVA"),
            ("XXXXXX00A00Z000S", "CODICEFISCALE"),
            ("IT60 X054 2811 1010 0000 0123 456", "IBAN"),
            ("Corso Buenos Aires 12", "INDIRIZZO"),
            ("ZZ000ZZ", "TARGA"),
        ],
        "must_not": ["committente", "fornitura"],
        # Pre-existing hole, declared: two spaces between groups stop the loose body at `IT60 X054`,
        # which is not a valid IBAN on its own. Rare (a copy/paste artefact), and re-opening `\s*`
        # between characters is what caused the over-match this pass fixed.
        "known_miss": [("IT60 X054  2811 1010 0000 0123 456", "double space between groups")],
    },
    {
        "name": "log-config",
        "entities": "",
        "catalogs": [],
        "patterns": None,
        "text": (
            "2026-01-01 10:00:00 ERROR auth failed for admin@northwind.local\n"
            "token=Bearer eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJzdWIiOiIxIn0.abc123def456ghi789\n"
            # AWS publishes this exact access-key ID as a documentation example; it is a
            # synthetic sentinel for shape detection, not a usable credential by itself.
            'api_key = "AKIAIOSFODNN7EXAMPLE"\n'
            "gateway 10.0.0.1 unreachable, dns gw.northwind.local\n"
        ),
        "must_find": [
            ("admin@northwind.local", "EMAIL"),
            ("eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJzdWIiOiIxIn0.abc123def456ghi789", "KEY"),
            ("AKIAIOSFODNN7EXAMPLE", "KEY"),
            ("10.0.0.1", "IP"),
            ("gw.northwind.local", "HOST"),
        ],
        "must_not": ["2026-01-01", "10:00:00"],
    },
    {
        "name": "prose-traps",
        "entities": "",
        # Only it-cities: with `products` on, Windows/Excel would be redacted — and that is a real
        # choice, not a defect, so the trap is tested with the catalog that is on.
        "catalogs": ["it-cities"],
        "patterns": None,
        "text": (
            "in via del tutto eccezionale, 3 volte l'anno\n"
            "il prato è verde, il corso d'acqua è largo 2 metri\n"
            "Windows Server 2019 aggiornato con Excel\n"
            "l'articolo costa 10 euro, il modello è fuori produzione\n"
        ),
        "must_find": [],
        "must_not": [
            "in via del tutto eccezionale, 3",
            "il prato è verde",
            "corso d'acqua",
            "Windows",
            "Excel",
        ],
        "declared_fp": [],
    },
    {
        "name": "declared-holes",
        "entities": "@type PERSONA\nPersona Test\n",
        "catalogs": [],
        "patterns": None,
        "text": (
            "Il cliente di Brescia ha chiesto un preventivo.\n"
            "Il nuovo responsabile, Persona NonInCatalogo, ha firmato il verbale.\n"
        ),
        "must_find": [],
        "must_not": [],
        "known_miss": [
            ("il cliente di Brescia", "contextual reference: no catalog, no marker"),
            ("Persona NonInCatalogo", "synthetic name absent from the dictionary"),
        ],
    },
    {
        "name": "contextual-city",
        "entities": "",
        "catalogs": ["it-cities"],
        "patterns": None,
        "text": "Trasferta ad Ancona per il collaudo del firewall.\n",
        "must_find": [],
        "must_not": [],
        "known_miss": [
            ("Ancona", "a city without the `sede di` marker: `it-cities` is context-gated by design"),
        ],
    },
]
