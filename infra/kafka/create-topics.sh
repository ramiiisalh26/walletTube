#!/bin/bash
set -e

BOOTSTRAP=kafka:9092

create_topic() {
  local TOPIC=$1
  local PARTITIONS=$2
  local RETENTION_MS=$3

  echo "Creating topic: $TOPIC (partitions=$PARTITIONS, retention=${RETENTION_MS}ms)"

  /opt/kafka/bin/kafka-topics.sh \
    --bootstrap-server $BOOTSTRAP \
    --create \
    --if-not-exists \
    --topic "$TOPIC" \
    --partitions "$PARTITIONS" \
    --replication-factor 1 \
    --config retention.ms="$RETENTION_MS"
}

# Direction: Java → Python
# Triggered when DB returns < 5 results for a query
create_topic "search.miss"              12   259200000    # 3 days

# Direction: Java → Python
# Triggered when user clicks a video result (auto-index it)
create_topic "user.video.watched"       12   259200000    # 3 days

# Direction: Java → Python
# Explicit index request (user submits a video URL)
create_topic "video.index.request"       6   604800000    # 7 days

# Direction: Java → Python
# Admin or system adds a new channel to crawl
create_topic "channel.discover.request"  3   604800000    # 7 days

# Direction: Python → Java
# Indexing finished — Java invalidates Redis cache for that query
create_topic "video.index.complete"      6    86400000    # 1 day

# Direction: Python → Java
# Worker failed to index a video (for alerting / dead-letter handling)
create_topic "index.error"               3   604800000    # 7 days

echo ""
echo "All topics created. Current topic list:"
/opt/kafka/bin/kafka-topics.sh --bootstrap-server $BOOTSTRAP --list