# Built-in catalogs

Catalog files use the same line-based format as custom entity dictionaries. They can be selected for a run with `--catalogs`; use `--list-catalogs` to see the available names.

## Format

```text
@type CITY
@match case-sensitive
@context (?:city of|office in)\s+
Example City
@context off
Another Example
```

Each directive applies to the entries that follow it until another directive replaces it. `@type` sets the placeholder type, `@match` selects case-sensitive or case-insensitive matching, `@context` limits a match to text preceded by a regular expression, and `@stem on` allows a value to match longer words with the same prefix. An unknown directive is an error.

## Use

```sh
python3 anon.py --list-catalogs
python3 anon.py report.txt --catalogs it-cities
python3 anon.py report.txt --catalogs it-cities,free-mail-domains
python3 anon.py report.txt --patterns legal
```

Pattern groups cover identity values, network values, and legal identifiers. Catalogs are optional because matching ordinary words or place names can remove useful context. Select only the lists that fit the document.

`it-cities.txt`, `vendors.txt`, and `products.txt` are starter lists, not complete registries. Review every result and maintain custom dictionaries for names that matter to your work. Do not treat a catalog match as proof that all identifying context has been removed.
