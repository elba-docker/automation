#!/bin/bash

# Script to build aggregate archives from multiple tests (for all types)
DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" >/dev/null 2>&1 && pwd )"

bash $DIR/build_archive.sh "." "d-c-50 d-c-100 d-m-50 d-m-100 d-mc-50 d-mc-100 d-r-50 d-r-100 d-rc-50 d-rc-100"
bash $DIR/build_archive.sh "." "i-c-50 i-c-100 i-m-50 i-m-100 i-mc-50 i-mc-100 i-r-50 i-r-100 i-rc-50 i-rc-100"
bash $DIR/build_archive.sh "." "ii-c-b ii-c-b ii-m-b ii-m-b ii-mc-b ii-mc-b ii-r-b ii-r-b ii-rc-b ii-rc-b"
bash $DIR/build_archive.sh "." "ii-c-s ii-c-s ii-m-s ii-m-s ii-mc-s ii-mc-s ii-r-s ii-r-s ii-rc-s ii-rc-s"
