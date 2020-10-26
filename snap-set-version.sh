#!/usr/bin/env bash

MN_CLI_GEM_VERSION=${1:-1.2.11}

case "$OSTYPE" in
	linux-gnu) SED=sed ;;
	darwin*) SED=gsed ;;
esac

${SED} -i "s/^version: '.*$/version: '${MN_CLI_GEM_VERSION}'/" snap/snapcraft.yaml