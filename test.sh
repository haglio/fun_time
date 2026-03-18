#!/usr/bin/env bash
set -e
.venv/Scripts/python.exe -m pytest "$@"
