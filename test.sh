#!/usr/bin/env bash
cd "$(dirname "$0")"
source ./script/setup.sh

./build-debug.sh -Xswiftc -warnings-as-errors
./swift-test.sh

./.debug/aquaspace-debug/aquaspace -h > /dev/null
./.debug/aquaspace-debug/aquaspace --help > /dev/null
./.debug/aquaspace-debug/aquaspace -v | grep -q "0.0.0-SNAPSHOT SNAPSHOT"
./.debug/aquaspace-debug/aquaspace --version | grep -q "0.0.0-SNAPSHOT SNAPSHOT"

./lint.sh
./generate.sh
./script/check-uncommitted-files.sh

echo
echo "✅ All tests have passed successfully"
