#!/bin/sh
PID=$(ps aux | grep 'tvpilot_server.py' | grep -v grep | awk '{print $2}' | head -1)
if [ -n "$PID" ]; then
  echo "Found tvpilot PID: $PID"
  sudo -n kill -9 $PID 2>&1
  sleep 2
fi
netstat -tlnp 2>/dev/null | grep 8090 || echo 'port 8090 free'
