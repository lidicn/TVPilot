#!/bin/bash
echo "=== bak.mjpeg ==="
cat /vol1/1000/docker/go2rtc/go2rtc.yaml.bak.mjpeg
echo ""
echo "=== 其他备份中的非iptv流 ==="
for f in /vol1/1000/docker/go2rtc/go2rtc.yaml.bak.*; do
    echo "--- $f ---"
    grep -E '^  [a-z]' "$f" | grep -v 'iptv_' | head -10
done
