#!/usr/bin/env python3
"""Thin entry-point shim -- see cgwatch/jsonapi.py for the implementation.

Kept at the repo root (rather than removed) so future Debian packaging
in debian/rules, which copies this file to /usr/bin/cgwatch-cli, keeps
working unchanged. `pip install` uses the `cgwatch-cli` console_scripts
entry point in setup.py instead, which points directly at
cgwatch.jsonapi:main.
"""

import sys

from cgwatch.jsonapi import main

if __name__ == "__main__":
    sys.exit(main())
