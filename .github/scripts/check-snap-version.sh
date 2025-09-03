#!/bin/bash

set -e

# Default values
SNAP_NAME="metanorma"
CHANNELS=""
VERSION=""
ARCHITECTURE=""

# Function to display usage
usage() {
    echo "Usage: $0 --channels CHANNELS --version VERSION --architecture ARCHITECTURE"
    echo ""
    echo "Options:"
    echo "  --channels CHANNELS      Comma-separated list of channels to check (e.g., 'edge,beta')"
    echo "  --version VERSION        Version to check for"
    echo "  --architecture ARCH      Architecture to check (e.g., 'amd64', 'arm64')"
    echo "  --help                   Display this help message"
    echo ""
    echo "Exit codes:"
    echo "  0 - Should publish (version not found in all specified channels)"
    echo "  1 - Should skip (version already exists in all specified channels)"
    echo "  2 - Error occurred"
    exit 1
}

# Parse command line arguments
while [[ $# -gt 0 ]]; do
    case $1 in
        --channels)
            CHANNELS="$2"
            shift 2
            ;;
        --version)
            VERSION="$2"
            shift 2
            ;;
        --architecture)
            ARCHITECTURE="$2"
            shift 2
            ;;
        --help)
            usage
            ;;
        *)
            echo "Error: Unknown option $1"
            usage
            ;;
    esac
done

# Validate required parameters
if [[ -z "$CHANNELS" || -z "$VERSION" || -z "$ARCHITECTURE" ]]; then
    echo "Error: Missing required parameters"
    usage
fi

echo "Checking if version '$VERSION' already exists in channels [$CHANNELS] for architecture '$ARCHITECTURE'"

# Get current status from snapcraft
echo "Getting current snap status..."
if ! status_output=$(snapcraft status "$SNAP_NAME" --arch "$ARCHITECTURE" 2>&1); then
    echo "Error: Failed to get snap status"
    echo "$status_output"
    exit 2
fi

echo "Current status:"
echo "$status_output"

# Convert comma-separated channels to array
IFS=',' read -ra CHANNEL_ARRAY <<< "$CHANNELS"

# Check each channel
all_channels_have_version=true
for channel in "${CHANNEL_ARRAY[@]}"; do
    # Trim whitespace
    channel=$(echo "$channel" | xargs)

    echo "Checking channel: $channel"

    # Parse the status output for this channel
    # Handle two formats:
    # 1) "latest amd64 stable 1.13.4 259" (version in field 4)
    # 2) "                 candidate 1.13.5 262" (version in field 2)
    channel_line=$(echo "$status_output" | grep -E "(^\s*${channel}\s+|latest\s+[^\s]+\s+${channel}\s+)" || echo "")
    if [[ "$channel_line" =~ latest.*${channel} ]]; then
        # First format: latest arch channel version revision
        channel_version=$(echo "$channel_line" | awk '{print $4}')
    else
        # Second format: channel version revision
        channel_version=$(echo "$channel_line" | awk '{print $2}')
    fi

    echo "Current $channel version: '$channel_version'"

    if [[ "$channel_version" != "$VERSION" ]]; then
        echo "Channel $channel does not have version $VERSION (has '$channel_version')"
        all_channels_have_version=false
    else
        echo "Channel $channel already has version $VERSION"
    fi
done

echo "Target version: '$VERSION'"

if [[ "$all_channels_have_version" == "true" ]]; then
    echo "✓ Version $VERSION already exists in ALL specified channels [$CHANNELS] for $ARCHITECTURE"
    echo "RESULT: SKIP PUBLISHING"
    exit 1
else
    echo "✓ Version $VERSION does not exist in all specified channels or needs update"
    echo "RESULT: SHOULD PUBLISH"
    exit 0
fi
