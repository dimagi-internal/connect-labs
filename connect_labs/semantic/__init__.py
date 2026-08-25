"""Semantic layer: Cube-syntax definitions compiled to SQL.

Nothing here runs Cube. The YAML under `registry/` uses Cube's measure/dimension
notation so a second engine can read the same definitions, and this package
compiles them to SQL that connect-labs executes itself.
"""
