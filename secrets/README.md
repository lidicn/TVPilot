# secrets/

ADB 私钥运行时挂载目录（本目录整体被 .gitignore 排除，绝不入库）。

- 把已授权的 `adbkey` / `adbkey.pub` 放到本目录
- `docker-compose.yml` 把它们只读挂到 `/root/.android/`
- Dockerfile 只 `mkdir -p /root/.android && chmod 700`，镜像内不含任何密钥
- 凭据零打印：不要在别处复制或回显私钥内容

密钥目前仍在仓根 `E:\NAS\TVPilot\adbkey`（已被 gitignore 排除）。
部署前由在场人员手工放入本目录；本 Bot 不搬运、不读取、不打印其内容。