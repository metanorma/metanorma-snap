#!/usr/bin/env bash

NEW_VERSION=${1:-1.2.11}
OLD_VERSION=$(yq e '.version' snap/snapcraft.yaml)
PACKED_MN_ROOT_URL=https://github.com/metanorma/packed-mn/releases/download
PACKED_MN_FILE=metanorma-linux-x86_64.tgz

if curl --output /dev/null --silent --head --fail "${PACKED_MN_ROOT_URL}/v${NEW_VERSION}/${PACKED_MN_FILE}"; then
  NEW_PACKED_MN_URL="${PACKED_MN_ROOT_URL}/v\$SNAPCRAFT_PROJECT_VERSION/${PACKED_MN_FILE}"
else # URL not exists
  NEW_PACKED_MN_URL=$(yq e '.parts.packed-mn.source' snap/snapcraft.yaml)

  if [[ $NEW_PACKED_MN_URL == *"SNAPCRAFT_PROJECT_VERSION"* ]]; then
	NEW_PACKED_MN_URL="${PACKED_MN_ROOT_URL}/v${OLD_VERSION}/${PACKED_MN_FILE}"
  fi
fi

yq e ".version = \"$NEW_VERSION\"" --inplace snap/snapcraft.yaml
yq e ".parts.packed-mn.source = \"${NEW_PACKED_MN_URL}\"" --inplace snap/snapcraft.yaml
