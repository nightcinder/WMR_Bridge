# Synthetic triage fixture

This is a small `wmr-ext-YYYYMMDD.zip` tree for `tools/triage_bundle.py` tests.
It is not a real tester export.

- The capture frames are copied verbatim from `samples/controller_frames.txt`
  (`sessionA-0` for the LEFT capture, `sessionB-0` for the RIGHT capture) and
  repeated 12 times so the still phase clears `analyze_capture.py`'s ten-frame
  minimum. The sample's real hand is unknown; the header hand is synthetic.
- `manifest.md`, `diagnostics.json` and the pose file follow the shapes the
  companion app writes (`test_bundle.dart`). Values are made up.
- The tests zip this directory; do not add generated output here.
