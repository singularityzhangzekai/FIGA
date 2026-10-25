#!/bin/zsh
set -eu
cd -- "${0:A:h}"
exec python3 -m grocery.monitor --mode deepseek --voice mic --pace 0.6 --start-delay 10
