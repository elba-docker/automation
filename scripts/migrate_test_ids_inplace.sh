#!/bin/bash

# Script to migrate test ids to zero pad them

TARGET_ID_LENGTH="$1"

shopt -s nullglob
for i in migrated/*.tar.gz; do
  filename=$(basename $i)
  archive_name="${filename%%.*}"
  echo "[$(date +%s)] [${archive_name}] Archive name: ${archive_name}.tar.gz"
  echo "[$(date +%s)] [${archive_name}] Renaming patterns"
  for i in $(seq 1 $TARGET_ID_LENGTH); do
    if [ "$i" = $TARGET_ID_LENGTH ]; then
      # Skip last iteration (no replacement needed)
      continue
    fi
    num_zeroes=$(($TARGET_ID_LENGTH-$i))
    zeroes=$(for i in $(seq 1 $num_zeroes); do echo -n '0'; done)
    find "./" -exec perl-rename "s/(.*)${archive_name}-(\d{${i}})(\D|$)/\$1${archive_name}-${zeroes}\$2\$3/" {} ";"
  done
done
