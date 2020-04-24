#!/bin/bash

# Script to migrate test ids to zero pad them

SRC_DIR="$1"
DEST_DIR="$2"
TARGET_ID_LENGTH="$3"

pushd () {
    command pushd "$@" > /dev/null
}

popd () {
    command popd "$@" > /dev/null
}

shopt -s nullglob
mkdir -p $DEST_DIR
pushd $SRC_DIR
for i in *.tar.gz; do
  archive_name="${i%%.*}"
  echo "[$(date +%s)] [${archive_name}] Archive name: ${archive_name}.tar.gz"
  temp_dir=$(mktemp -d)
  echo "[$(date +%s)] [${archive_name}] Extracting to ${temp_dir}"
  tar -C $temp_dir -xvf "$i" > /dev/null
  echo "[$(date +%s)] [${archive_name}] Renaming patterns"
  for i in $(seq 1 $TARGET_ID_LENGTH); do
    if [ "$i" = $TARGET_ID_LENGTH ]; then
      # Skip last iteration (no replacement needed)
      continue
    fi
    num_zeroes=$(($TARGET_ID_LENGTH-$i))
    zeroes=$(for i in $(seq 1 $num_zeroes); do echo -n '0'; done)
    find $temp_dir -exec perl-rename "s/(.*)${archive_name}-(\d{${i}})(\D|$)/\$1${archive_name}-${zeroes}\$2\$3/" {} ";" > /dev/null 2>&1
    find $temp_dir \( -type d -name .git -name "*.tar.gz" -prune \) -o -type f -print0 | xargs -0 sed -i "s/${archive_name}-(\d{${i}})(\D|$)/${archive_name}-${zeroes}\$1\$2/g" > /dev/null 2>&1
  done

  echo "[$(date +%s)] [${archive_name}] Entering temp folder"
  pushd $temp_dir
  echo "[$(date +%s)] [${archive_name}] Re-creating archive"
  tar -czvf "${archive_name}.tar.gz" * > /dev/null
  echo "[$(date +%s)] [${archive_name}] Exiting temp folder"
  popd
  echo "[$(date +%s)] [${archive_name}] Moving archive from '$temp_dir/${archive_name}.tar.gz' to '$DEST_DIR/${archive_name}.tar.gz'"
  mv "$temp_dir/${archive_name}.tar.gz" $DEST_DIR
  echo "[$(date +%s)] [${archive_name}] Deleting temp folder"
  rm -rf $temp_dir
done
popd
