"""MBW v1↔v3 parity test harness.

Compares the dashboard payload produced by v1 (Python helpers in
templates/mbw_monitoring/) to the payload produced by v3
(pipeline-native template, parity-tested against v1 before any cutover).
The contract in payload_contract.py defines what equivalence means;
tolerance is leaf-by-leaf.

v2 (mbw_monitoring_v2 + job handler) is not part of the parity contract —
it stays frozen alongside v1 as a transitional implementation until v3
has been proven in production.
"""
