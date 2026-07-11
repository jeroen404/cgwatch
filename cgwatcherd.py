#!/usr/bin/env python3
"""Thin entry-point shim -- see cgwatch/daemon.py for the implementation.

Kept at the repo root (rather than removed) so the Debian packaging in
debian/rules, which copies this file to /usr/bin/cgwatcherd, keeps working
unchanged. `pip install` uses the `cgwatcherd` console_scripts entry point
in setup.py instead, which points directly at cgwatch.daemon:main.
"""

from cgwatch.daemon import main

if __name__ == "__main__":
    main()
