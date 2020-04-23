#!/bin/bash

# Script to build aggregate archives from multiple tests

TEST_PATH="$1"
TEST_NAMES="$2"
for TEST_NAME in $TEST_NAMES; do
    TAR_PATH="$TEST_PATH/archive/$TEST_NAME.tar.gz"
    echo "Building archive for $TEST_NAME at $TAR_PATH"
    tar -czvf $TAR_PATH $TEST_PATH/config.yml $TEST_PATH/logs/$TEST_NAME-*.log $TEST_PATH/results/$TEST_NAME-*.tar.gz $TEST_PATH/working/$TEST_NAME-*
done
