import sys
sys.path.insert(0, '/app')
from tvpilot_server import generate_channel_logo
png = generate_channel_logo('中央一台')
print('type:', type(png))
print('len:', len(png))
print('header hex:', png[:8].hex())
valid = png[:8] == bytes([0x89, 0x50, 0x4E, 0x47, 0x0D, 0x0A, 0x1A, 0x0A])
print('is_valid_png:', valid)
