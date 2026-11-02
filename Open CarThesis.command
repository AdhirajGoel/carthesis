#!/bin/zsh
set -eu
cd -- "${0:A:h}"
exec /usr/bin/open web/index.html
