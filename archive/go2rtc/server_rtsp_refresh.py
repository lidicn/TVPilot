def _go2rtc_opener():
    import urllib.request
    if not GO2RTC_AUTH_USER or not GO2RTC_AUTH_PASS:
        raise RuntimeError('go2rtc 凭据未配置（需环境变量 GO2RTC_USER / GO2RTC_PASS）')
    mgr = urllib.request.HTTPPasswordMgrWithDefaultRealm()
    mgr.add_password(None, GO2RTC_BASE, GO2RTC_AUTH_USER, GO2RTC_AUTH_PASS)
    return urllib.request.build_opener(urllib.request.HTTPBasicAuthHandler(mgr))

def _do_refresh_rtsp():
    """后台执行 RTSP 刷新：抓1个频道 → 提取用户token → 批量更新 go2rtc 流。"""
    import re
    from urllib.parse import urlparse, parse_qs, unquote, quote

    st = _refresh_rtsp_state
    st['running'] = True
    st['message'] = '开始刷新...'
    log_prefix = '[RTSP-Refresh]'

    def log(msg):
        print(f'{log_prefix} {msg}', flush=True)
        st['message'] = msg

    try:
        # 1. 启动电信 IPTV，等待它真正在前台
        log('启动电信 IPTV 应用...')
        adb_cmd(f'shell am start -n {IPTV_PKG}/{IPTV_ACT}', timeout=10)
        # 等待电信 IPTV 在前台（最多30秒）
        for i in range(15):
            time.sleep(2)
            fg = adb_shell_output('dumpsys window | grep mCurrentFocus', timeout=5)
            if IPTV_PKG in fg:
                log(f'  电信 IPTV 已到前台（{i*2+2}秒）')
                break
        else:
            log('  警告: 电信 IPTV 未到前台，继续尝试...')

        # 2. 清空 logcat
        adb_cmd('logcat -c', timeout=10)
        time.sleep(1)

        # 3. 换台到频道1
        log('切换到频道1...')
        adb_cmd('shell input keyevent KEYCODE_1', timeout=5)
        time.sleep(0.5)
        adb_cmd('shell input keyevent KEYCODE_ENTER', timeout=5)

        # 4. 等待播放日志
        log('等待播放日志（15秒）...')
        time.sleep(15)

        # 5. 抓取 RTSP URL
        log('抓取 RTSP URL...')
        logcat = adb_shell_output('logcat -d -s CTCMediaPlayer', timeout=15)
        rtsp_urls = re.findall(r'rtsp://[^\s"\']+', logcat)
        if not rtsp_urls:
            logcat = adb_shell_output('logcat -d', timeout=15)
            rtsp_urls = re.findall(r'rtsp://[^\s"\']+', logcat)

        if not rtsp_urls:
            log('未找到 RTSP URL！')
            st['last_success'] = False
            return

        rtsp_url = rtsp_urls[-1]
        log(f'抓到: {rtsp_url[:70]}...')

        # 6. 解析 accountinfo
        parsed = urlparse(rtsp_url)
        params = parse_qs(parsed.query)
        ai = unquote(params['accountinfo'][0])
        parts = ai.split('~')
        if len(parts) < 6:
            log(f'accountinfo 段数不足: {len(parts)}')
            st['last_success'] = False
            return

        user_token = parts[4]
        ext = parts[5]
        ext_parts = ext.split(':')
        ext_data = ':'.join(ext_parts[1:])
        ext_fields = ext_data.split(',')
        timestamp = ext_fields[0]
        log(f'token={user_token[:20]}... ts={timestamp}')

        # 7. 获取 go2rtc 所有流
        log('获取 go2rtc 流列表...')
        opener = _go2rtc_opener()
        resp = opener.open(f'{GO2RTC_BASE}/api/streams', timeout=10)
        streams = json.loads(resp.read())

        # 8. 生成新的 go2rtc.yaml（保留所有流，只更新 iptv 的 RTSP URL）
        log('生成 go2rtc 配置...')
        yaml_lines = [
            'api:',
            '  listen: ":1984"',
            '  origin: "*"',
            '',
            'log:',
            '  level: info',
            '  streams: error',
            '',
            'streams:',
            '',
        ]
        updated = 0
        for name, info in streams.items():
            producers = info.get('producers', [])
            if not producers:
                continue
            purl = producers[0].get('url', '')

            if name.startswith('iptv_') and purl.startswith('exec:ffmpeg'):
                # IPTV 流：替换 RTSP URL 中的 token
                m = re.search(r'-i\s+"?(rtsp://[^"]+)"?', purl)
                if not m:
                    continue
                old_rtsp = m.group(1)
                old_parsed = urlparse(old_rtsp)
                old_params = parse_qs(old_parsed.query)
                if 'accountinfo' not in old_params:
                    continue
                old_ai = unquote(old_params['accountinfo'][0])
                old_parts = old_ai.split('~')
                if len(old_parts) < 6:
                    continue

                old_ext = old_parts[5]
                old_ext_parts = old_ext.split(':')
                old_ext_data = ':'.join(old_ext_parts[1:])
                old_ext_fields = old_ext_data.split(',')
                new_ext_fields = old_ext_fields.copy()
                new_ext_fields[0] = timestamp
                if len(new_ext_fields) > 3:
                    new_ext_fields[3] = timestamp
                new_ext = f'{old_ext_parts[0]}:{",".join(new_ext_fields)}'
                new_ai = f'~~V2.0~{old_parts[2]}~{user_token}~{new_ext}'
                new_ai_enc = quote(new_ai, safe='')

                new_query_params = old_params.copy()
                new_query_params['accountinfo'] = [new_ai_enc]
                new_query = urllib.parse.urlencode(new_query_params, doseq=True)
                new_rtsp = f'{old_parsed.scheme}://{old_parsed.netloc}{old_parsed.path}?{new_query}'

                new_producer = (
                    f'exec:ffmpeg -rtsp_transport tcp -i "{new_rtsp}" '
                    f'-fflags nobuffer -flags low_delay -codec:v copy '
                    f'-codec:a aac -b:a 128k -ar 48000 -ac 2 -rtsp_transport tcp -f rtsp {{output}}'
                )
                yaml_lines.append(f'  {name}:')
                yaml_lines.append('')
                yaml_lines.append(f'  - {new_producer}')
                yaml_lines.append('')
                updated += 1
            else:
                # 非 IPTV 流（摄像头等）：原样保留
                yaml_lines.append(f'  {name}:')
                yaml_lines.append('')
                yaml_lines.append(f'  - {purl}')
                yaml_lines.append('')

        yaml_content = '\n'.join(yaml_lines)
        # F3/F18: 配置写到外挂 data 卷（持久可审计），不再写永不清理的 /tmp/go2rtc_refreshed.yaml
        try:
            os.makedirs(DATA_DIR, exist_ok=True)
        except Exception as _e:
            log(f"[go2rtc] data 目录不可用，配置写盘将失败: {_e}")
        data_yaml = os.path.join(DATA_DIR, 'go2rtc_refreshed.yaml')
        with open(data_yaml, 'w', encoding='utf-8') as f:
            f.write(yaml_content)
        log(f'配置已生成: {len(yaml_content)} 字节, {updated} 个 IPTV 流已更新 -> {data_yaml}')

        # 9. 部署到 go2rtc（F3: 仅当 docker.sock 存在才允许 cp/restart；拔 sock 后降级为只产 yaml）
        if os.path.exists(DOCKER_SOCK):
            log('检测到 docker.sock，部署并重启 go2rtc...')
            subprocess.run(['docker', 'cp', data_yaml, 'go2rtc:/config/go2rtc.yaml'],
                           capture_output=True, timeout=10)
            r2 = subprocess.run(['docker', 'restart', 'go2rtc'],
                                capture_output=True, text=True, timeout=30)
            if r2.returncode != 0:
                log(f'go2rtc 重启失败: {(r2.stderr or "")[:120]}')
                st['running'] = False
                st['last_success'] = False
                return
            time.sleep(5)
            st['docker_used'] = True
            log('go2rtc 已重启')
        else:
            # docker.sock 已拔除：不触碰 docker，只产出 yaml，等待 go2rtc 侧（外挂配置/API/人工）重载
            st['docker_used'] = False
            log('未挂载 docker.sock，已跳过 docker cp/restart；yaml 在外挂卷，需 go2rtc 侧重载')

        # 9. 外挂模式：不切回 mytv，IPTV 在前台播放
        # time.sleep(3)
        # log('切回 mytv...')
        # adb_cmd(f'shell am start -n {MYTV_PKG}', timeout=10)

        st['running'] = False
        st['requires_reload'] = not st.get('docker_used', False)
        # MV-1: yaml 写出 不等于 流已生效；无 docker.sock 时 go2rtc 未重载，last_success 必须 False
        st['last_success'] = bool(st.get('docker_used'))
        if st.get('docker_used'):
            log(f'完成！{updated} 个频道已刷新并重启 go2rtc')
        else:
            log(f'完成！{updated} 个频道已写入 {data_yaml}；但 go2rtc 未重载，流尚未生效，需其侧重载')

    except Exception as e:
        log(f'错误: {e}')
        st['running'] = False
        st['last_success'] = False
        try:
            adb_cmd(f'shell am start -n {MYTV_PKG}', timeout=10)
        except Exception:
            pass

