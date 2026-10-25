#!/bin/zsh
set -eu
cd -- "${0:A:h}"
if [[ -x .venv/bin/python3 ]]; then
  grocery_python=.venv/bin/python3
elif [[ -x /Library/Frameworks/Python.framework/Versions/3.13/bin/python3 ]]; then
  grocery_python=/Library/Frameworks/Python.framework/Versions/3.13/bin/python3
else
  grocery_python=python3
fi
exec "$grocery_python" -m grocery demo --reset --mode deepseek --voice mic
