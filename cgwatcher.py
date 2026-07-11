#!/usr/bin/env python3
"""Thin entry-point shim -- see cgwatch/tui.py for the implementation.

Kept at the repo root (rather than removed) so the Debian packaging in
debian/rules, which copies this file to /usr/bin/cgwatcher, keeps working
unchanged. `pip install` uses the `cgwatcher` console_scripts entry point
in setup.py instead, which points directly at cgwatch.tui:main.
"""

from cgwatch.tui import main

if __name__ == "__main__":
    main()
