#!/bin/bash

# Script to build aggregate archives from multiple tests

TEST_NAME="$1"
TAR_PATH="./archive/$TEST_NAME.tar.gz"
echo "Building archive for $TEST_NAME at $TAR_PATH"
tar -czvf $TAR_PATH ./ii.yml ./logs/$TEST_NAME-*.log ./results/$TEST_NAME-*.tar.gz ./working/$TEST_NAME-*
