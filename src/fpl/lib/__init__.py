"""Shared kernel for the pipeline.

One module per concern, no base classes: a source is a script that calls these,
not a subclass that inherits from them.

    http.py     descriptive User-Agent, per-host throttle, jittered retry

Planned:
    bronze.py   write_bronze() — atomic write, sha256, manifest merge  (FOO-25)
    sources.py  load and enforce data/bronze/manual/sources.csv        (FOO-26)
"""
