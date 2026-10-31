#!/usr/bin/env bash
# The check every data workflow runs before it commits, from database/:
# validate the whole record set (both harvesters); if that fails, quarantine
# the failing records and validate again; if the SMI tracker's files are
# still broken, discard its uncommitted changes so they cannot hold up the
# rest, and validate once more. A non-zero exit means nothing is committed.
set -u
python pipeline/validate_data.py && exit 0
python pipeline/quarantine.py && python pipeline/validate_data.py && exit 0
echo "SMI data failed validation: its changes are discarded, not committed"
git checkout -- ../smi/data
git clean -fdq ../smi/data
python pipeline/validate_data.py
