# TVPilot — 电视设备控制服务 v0.9
FROM alpine:3.19
RUN apk add --no-cache python3 android-tools py3-paho-mqtt py3-pillow tesseract-ocr tesseract-ocr-data-chi_sim font-wqy-zenhei
WORKDIR /app
COPY tvpilot_server.py /app/tvpilot_server.py
COPY hdmi3_monitor.py /app/hdmi3_monitor.py
COPY stats.py /app/stats.py
COPY alerts.py /app/alerts.py
COPY channels.json /app/channels.json
# 模块化包
COPY tvpilot/ /app/tvpilot/
# PWA 静态资源
COPY static/ /app/static/
# ADB 私钥由 docker-compose.yml 运行时只读挂载（A-TV-02：不进镜像/构建上下文）
RUN mkdir -p /root/.android && chmod 700 /root/.android
EXPOSE 8090
HEALTHCHECK --interval=30s --timeout=8s --start-period=10s --retries=3 \
  CMD python3 -c "import urllib.request; urllib.request.urlopen('http://localhost:8090/api/health', timeout=5)" || exit 1
CMD ["python3", "/app/tvpilot_server.py"]
