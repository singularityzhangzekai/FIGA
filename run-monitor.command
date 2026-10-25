#!/bin/zsh
set -eu
cd -- "${0:A:h}"
exec python3 -m grocery.monitor --mode replay --voice scripted --speak --pace 1.1 --start-delay 10
