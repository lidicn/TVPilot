# 电信 IPTV 认证 API 逆向分析（方案 B 存档）
> 状态（2026-11-12 整理）：**历史记录**。本文记录当时的事实，不代表当前状态；现行口径见 `doc/README.md` 真源优先级表。

> 状态：部分完成（卡在 clientId）
> 日期：2026-09-09
> 用途：后续继续方案 B 开发时参考，避免重复逆向

---

## 一、背景

电信 IPTV 的 RTSP 地址带有认证 token（accountinfo），约 4 天过期。
- **方案 A**（已上线）：通过 adb logcat 抓电信 IPTV 应用换台日志，提取最新 RTSP URL，每 3 天自动刷新。
- **方案 B**（本档）：逆向认证 API，纯 HTTP 请求获取 access_token，完全不依赖电视应用。

---

## 二、认证流程（已逆向 90%）

参考源码：`com.chinatelecom.iptv.sdk.impl.LoginApiImpl.java`（反编译自电信 IPTV APK）

### 步骤 1：获取 EPG 服务器地址 ✅

```
GET http://eds.iptv.gd.cn:8082/EDS/jsp/AuthenticationURL?Action=Login&return_type=1&UserID={userId}
```

返回 JSON：
```json
{"epgurl":"http://183.59.160.50:8082/EPG/jsp/AuthenticationURL?Action=Login&return_type=1&UserID=xxx&v6edsaddress=59.37.212.9"}
```

提取 EPG 服务器：`http://183.59.160.50:8082`（IP 可能变化，每次动态获取）

### 步骤 2：获取 EncryToken ❌ 卡住

```
GET {epg_server}/EPG/oauth/v2/authorize?response_type=EncryToken&client_id={clientId}&userid={userId}
```

**当前阻塞**：返回 `{"error":"invalid_client", "error_description":"客户端授权失败"}`

已尝试的 clientId 候选值（全部失败）：
- MAC 地址（有冒号/无冒号）：`04:7A:0B:AD:61:03` / `047A0BAD6103`
- stbId：`00106144000300100001047A0BAD6103`
- 应用包名：`com.android.smart.terminal.iptv`
- 默认 IMEI：`00000000000`
- 设备类型：`SoftTV-B` / `MiTV-MFTP0`
- 品牌：`xiaomi`
- 用户 ID：`075542669782`
- mpLicense：`376330A2B5EEE6EB`

**clientId 可能来源**（待排查）：
1. SDK 级别的固定注册值（需从 SDK 文档或更深逆向中找）
2. 从 EPG 服务器的其他 API 动态获取
3. 应用首次激活时从服务器注册获得
4. 在其他 dex/so 库中（APK 只有一个 classes.dex，可能在 native 库中）

**下一步建议**：在电视上用 mitmproxy 抓包，捕获应用真实发送的 EncryToken 请求，提取正确的 clientId。

### 步骤 3：构造 authinfo 并 DES 加密 ✅

明文格式（来自 LoginApiImpl.java 第 35 行）：
```
str2Enc = "{random}${encryToken}${userId}${IMEI}${IP}${MAC}$$OTT"
```

各字段说明：
- `random`：7 位随机数 `(int)(Math.random() * 1.0E7)`
- `encryToken`：步骤 2 获取
- `userId`：用户 ID
- `IMEI`：设备 IMEI（电视无电话功能，可能返回 `00000000000`）
- `IP`：设备电信内网 IP（如 `10.90.6.4`，DHCP 动态分配）
- `MAC`：设备 MAC 地址（如 `04:7A:0B:AD:61:03`）

加密算法：
```java
md5pass = MD5(password)                    // 32 字符十六进制
key = genDESKey(md5pass.substring(0, 24).getBytes())  // 取前 24 字符，转 8 字节 DES 密钥
enc = desEncrypt(key, str2Enc)            // DES/ECB/PKCS5Padding，Base64 输出
```

Python 实现：
```python
import hashlib, base64
from Crypto.Cipher import DES
from Crypto.Util.Padding import pad

md5pass = hashlib.md5(password.encode()).hexdigest()
key = md5pass[:24].encode()[:8]  # DES 密钥 8 字节
cipher = DES.new(key, DES.MODE_ECB)
enc = base64.b64encode(cipher.encrypt(pad(str2enc.encode(), 8))).decode()
```

### 步骤 4：获取 access_token ⏳

```
GET {epg_server}/EPG/oauth/v2/token?grant_type=EncryToken
    &client_id={clientId}
    &UserID={userId}
    &DeviceType={deviceType}
    &DeviceVersion={deviceVersion}
    &userdomain=1
    &datadomain=3
    &accountType={accountType}
    &authinfo={enc}
```

预期返回：
```json
{"access_token":"xxx", "refresh_token":"xxx", "expires_in":xxx}
```

**`access_token` 就是 RTSP URL 中 accountinfo 的段4（用户级 token），全局共享，约 4 天过期。**

### 步骤 5：批量更新频道 ✅

拿到 access_token 后，批量替换所有频道 RTSP URL 中 accountinfo 的段4和时间戳，逻辑同方案 A。

---

## 三、已知设备参数（从 logcat 提取）

| 参数 | 值 | 来源 |
|------|-----|------|
| userId | `075542669782` | logcat `AuthData.userID` |
| password | `34284432` | logcat `AuthData.password` |
| authURL | `http://125.88.42.180:33200/EPG/jsp/AuthenticationURL` | logcat `AuthData.authurl`（应用用这个，SDK 用 eds 入口） |
| stbId | `00106144000300100001047A0BAD6103` | logcat `AuthUtils.toAuthInfo` |
| MAC | `04:7A:0B:AD:61:03` | logcat `Device.strMacAddr` |
| IP（电信内网） | `10.90.6.4`（DHCP 动态，会变） | logcat 统计日志 |
| IMEI | 未知（电视无电话功能，可能 `00000000000`） | `TelephonyManager.getDeviceId()` |
| itvid | `003` | logcat authinfo |
| version | `1.0.6` | logcat authinfo |
| areaNum | `4400` | logcat authinfo |
| tvType | `MiTV-MFTP0` | logcat `AuthUtils.toAuthInfo` |
| tvBrand | `xiaomi` | logcat `AuthUtils.toAuthInfo` |
| stbtype | `SoftTV-B` | logcat 统计日志 |
| mpLicense | `376330A2B5EEE6EB` | logcat `AuthUtils.toAuthInfo` |
| systemVersion | `9` | logcat `AuthUtils.toAuthInfo` |
| cpuInfo | `m7642` | logcat `AuthUtils.toAuthInfo` |
| totalMem | `3 GB` | logcat `AuthUtils.toAuthInfo` |
| totalFlash | `22 GB` | logcat `AuthUtils.toAuthInfo` |
| netType | `有线` | logcat `AuthUtils.toAuthInfo` |

---

## 四、RTSP URL accountinfo 结构（已验证）

```
rtsp://{host}/PLTV/88888895/224/{channel_id}/{stream_id}_0.smil
  ?rrsip=125.88.104.40
  &zoneoffset=480
  &icpid=szmg
  &limitflux=-1
  &limitdur=-1
  &tenantId=8601
  &GuardEncType=2
  &accountinfo=~~V2.0~{段3}~{段4}~{段5}
```

- **段3**：频道级 token，每频道不同，**永不过期**
- **段4**：用户级 token（即 access_token），**全局共享，约 4 天过期**
- **段5**：`ExtInfoPC{base64}:{时间戳},{用户ID},{IP},{时间戳},{频道代码},...`

---

## 五、相关文件

- 电信 IPTV APK：`D:\Documents\WorkSpace\TV_CAM\tmp\iptv_apk\iptv_base.apk`
- 反编译源码：`D:\Documents\WorkSpace\TV_CAM\tmp\iptv_apk\decompiled\`
- 认证测试脚本：`D:\Documents\WorkSpace\TV_CAM\tmp\test_auth_api.py`
- clientId 尝试脚本：`D:\Documents\WorkSpace\TV_CAM\tmp\try_client_ids.py`
- 关键类：`com.chinatelecom.iptv.sdk.impl.LoginApiImpl.java`
- 关键类：`com.chinatelecom.iptv.sdk.IPTVApplication.java`（getIMEI/getIp/getLocalMacAddress）
- 关键类：`com.chinatelecom.iptv.sdk.IPTVConstant.java`（API 路径常量）

---

## 六、后续继续方案 B 的步骤

1. **抓包获取 clientId**：在电视上设置 mitmproxy 代理，捕获电信 IPTV 应用发送的 `/EPG/oauth/v2/authorize` 请求，提取正确的 clientId
2. **验证完整流程**：用正确的 clientId 跑通步骤 2-4，确认 access_token 与 logcat 中 accountinfo 段4一致
3. **编写方案 B 脚本**：纯 HTTP 认证获取 token，批量更新频道，部署到 NAS 替代方案 A
4. **优势**：完全不依赖电视应用，可随时刷新，刷新时间从 40 秒缩短到 5 秒
