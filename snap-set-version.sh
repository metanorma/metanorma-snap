#!/usr/bin/env bash

MN_CLI_GEM_VERSION=${1:-1.2.11}

case "$OSTYPE" in
	linux-gnu) SED=sed ;;
	darwin*) SED=gsed ;;
esac

REPLACE_MARKER_BEGIN="# > snap-set-version.sh #"
REPLACE_MARKER_END="# < snap-set-version.sh #"
${SED} -i "/${REPLACE_MARKER_BEGIN}/,/${REPLACE_MARKER_END}/c${REPLACE_MARKER_BEGIN}\nversion: '${MN_CLI_GEM_VERSION}'\n${REPLACE_MARKER_END}" snap/snapcraft.yaml