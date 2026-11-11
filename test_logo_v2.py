import sys
sys.path.insert(0, '/app')
from tvpilot_server import generate_channel_logo

png = generate_channel_logo('中央一台')
print('CCTV1:', len(png) if png else 'FAIL')

png2 = generate_channel_logo('湖南卫视')
print('Hunan:', len(png2) if png2 else 'FAIL')

png3 = generate_channel_logo('CCTV4K超高清')
print('CCTV4K:', len(png3) if png3 else 'FAIL')

# 保存到文件供查看
if png:
    with open('/tmp/cctv1.png', 'wb') as f:
        f.write(png)
    print('Saved /tmp/cctv1.png')
