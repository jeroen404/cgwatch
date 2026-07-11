#!/bin/bash
exec env PYTHONPATH=/home/jack/gitrepos/cgwatch python3 -m cgwatch.jsonapi "$@"
