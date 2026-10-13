"""Dataset connectors. Each connector:

* reads its spec from ``data_sources.yaml`` (never invents URLs or field names),
* downloads/caches the source (or reads a user-supplied local file),
* validates schema and records,
* writes to PostGIS + Parquet and records provenance in ``hazard_sources``.
"""
