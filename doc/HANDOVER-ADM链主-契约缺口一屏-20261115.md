# TVPilot → ADM-链主 交接一屏：C1–C6 盘点的 6 项契约缺口现状

> 2026-11-15 12:0x ｜ 整理：TVPilot 维护 Bot ｜ 经 Chief 转 ADM-链主
> 口径：**按当前状态写实**，不照抄 11-14 盘点时的「待裁」旧文本——G1/G2/G3/G5/G6 已裁定或采认，
> 只有凭据相关项因 Chief 12:0x 令「凭据暂不给」重新挂起。
> 纪律：只读、不提交、不 push、真机不动；凭据零打印零落盘；汇报线改 Chief（⛔ 不再回 AF-PM）。

---

## 一、6 项缺口一屏（G1–G6，来自盘点报告 §二）

| # | 缺口 | 当前状态 | 依据 / 落点 |
|---|---|---|---|
| G1 | §四 S7 同帧硬条件 vs 现网 IPTV 原生链路 | ✅ **已裁定闭合**（G1＝纯 A）：契约升 v2.1→**v2.2**；链路＝唤醒→ADB→IPTV→CCTV1；§四 同帧＝S3+S4，S7＝`unknown(not_applicable_iptv_native)`；faceid/tvdesk 自动化推二期 | 出资人 11-14 23:5x 卡面亲选；契约 L13/L29/L51/L74 |
| G2 | §十三「HTTP 直调」范围外 vs 预案 D3 `POST /tv/morning_news` | ✅ **已采认默认闭合**：命令面＝HA `button.tv_morning_news`（MQTT），编排内聚内部函数；对外只留 `GET /af/v1/morning_news/last`；`POST /tv/morning_news` 不做 | AF-PM 11-14 21:3x 回票；配合预案 v2.2 §〇 |
| G3 | §六 faceid 隐私 vs 仓内 `/api/face/backup` 持久化 | ✅ **自行收口报备**：一期链路不调用该端点，列 D1 鉴权收口清单，不删功能；S6 只发 who+at+source、`retention=last` | 盘点产出 §二 G3；`af_discovery.py` 属性 |
| G4 | C3/C4 唤醒通道零实现＋凭据未给 | ⚠️ **设计已采认、实现与凭据挂起**：唤醒列 D3 首件（不采「一期不做唤醒」回退）；但 **miIO token 现暂不下发**；WoL MAC／设置项实测等出资人在场 | Chief 12:0x 令①；配合预案 v2.2 §一 |
| G5 | C6 broker 未定＋MQTT 桥未连 | ⚠️ **broker 侧已验证闭合、HA 集成重新挂起**：mosquitto `tvpilot` 账号已建并 AUTH_TEST_OK、部署 `.env` 就位、compose 补 4 键、容器 00:04 由部署侧重建、`mqtt_connected=true`、rc=5 计数 0；但 **HA broker 发布凭据／HA 集成凭据现暂不下发**，D1 discovery 发布维持 ⛔ | `data/reports/AF_V21_BROKER_VERIFY_20261115.md`；Chief 12:0x 令① |
| G6 | 息屏 24h「每 2h 现读」vs §五 ⛔ 周期探测 | ✅ **已采认＋加硬要求，真机执行挂起**：定性为一次性／在场授权／`experiment` 标注／结束即停；每 2h 现读必须记 `{screen_before, screen_after, screen_changed, degraded}`，若现读唤醒屏幕即退两点式回退并如实记录。离线设计与矩阵已绿 | `doc/DESIGN-AF联动D1D2-...md` §四；`AF_D1D2_PROBE_MATRIX_20261114.json` |

**第 7 处（11-15 新增，已闭合）**：纯 A 失败路径无诚实可落 result ⇒ 裁定追加 `iptv_fail`（契约 **v2.2**），
IPTV 启动失败／选台失败／同帧判据不通过三路径共用一值，AF 话术「电视已打开，新闻没能自动打开，请手动观看」，
§四 S3/S4 如实。记档 `af_morning_news.py` 的 `RESOLVED_GAPS`。

## 二、C1–C6 只读盘点结论（链主对齐用，全部为文件/代码层真值）

| # | 项 | 结论 |
|---|---|---|
| C1 | 统一指令中心 | `tvpilot/` 0 命中，仅工单与 `routes/agent.py`（蓝图/规划器）；D3 端点需新开 |
| C2 | ADB 通道 | `tvpilot/adb.py`：adb binary＋`192.168.2.238:5555` 持久化 shell；四元探针需新写；`state.py:133-149` `dumpsys power` 可作 S1 |
| C3 | miIO | 产品代码 0 命中；token **暂不给** |
| C4 | WoL | 产品代码 0 命中；MAC／设置项未实测（等在场） |
| C5 | tvdesk / ArcFace / IPTV | IPTV `com.android.smart.terminal.iptv`、中央一台＝台号 21、`/api/current` 读 STB `LastchannelID`（**≤6s 写回延迟**，AGENTS.md 在案）；tvdesk 无对外端点、ArcFace 判定二期；`health.py` 不查 ArcFace ⇒ S5 需新建 |
| C6 | broker | `192.168.2.200:1883`；部署侧账号/凭据/重建已验证（见 G5）；HA 集成与 discovery 发布未放行 |

## 三、交接要点（链主接手需知道）

1. **契约文件**：`E:\NAS\AutoForge\docs\AF_TVPilot_联动契约_v1_20261114.md`——**文件名 v1／内容 v2.2**（版本标识异议在案，未更名）。冻结点＝v2.2 纯 A＋`iptv_fail`。
2. **盘点母本**：`E:\NAS\TVPilot\doc\RPT-AF联动契约v2_确认与盘点-20261114.md`（142 行，含 §九 G1 裁定同步＋v2.2 补记）。
3. **设计/证据**：`doc/DESIGN-AF联动D1D2-C2四元探针与S5判定面-20261114.md`；`data/reports/AF_D1D2_PROBE_MATRIX_20261114.json`、`AF_D3_MATRIX_20261115.json`（41/41 全绿）、`AF_V21_BROKER_VERIFY_20261115.md`。
4. **已落码（未提交）**：`tvpilot/af_morning_news.py`（D3 编排）、`tvpilot/af_discovery.py`（D1 discovery，**发布闸恒关**）、`data/reports/af_d3_matrix_20261115.py`；`docker-compose.yml`／`.env.example` 补 4 键（只放变量引用与占位模板）。
5. **缓办项**：`GET /af/v1/morning_news/last` 路由挂载等并行会话收口（9 个 `tvpilot/*.py` 正被编辑，`git status` M=72／??=37）；4 个新文件＋compose 补键按纪律停在手上不 commit。
6. **凭据与真机**：HA broker 发布凭据、HA 集成凭据、电视 miIO token **一律暂不下发**；唤醒/选台/息屏 24h/联调全部等出资人在场或亲口授权。D1 discovery 发布维持 ⛔。
7. **现状**：HEAD `7299938`、23 笔提交、零 push；只读、不提交、不部署；零真机、零 MQTT 发布、零凭据读取/打印/落盘。

## 四、纪律

本件为交接说明，纯只读整理＋落一文件；⛔ 未改契约条款／接口形状／枚举，未碰真机，未提交未推送，未进 AF 台账。
后续汇报线＝Chief（经其转 ADM-链主），⛔ 不再回 AF-PM。
