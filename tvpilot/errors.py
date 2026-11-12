"""TVPilot 错误码常量。

所有 tool_* 函数返回三元组 (result, error, message)，error 字段使用本模块常量。
绞杀者模式第一阶段迁移：零依赖，可独立 import。
"""

ERR_TV_UNREACHABLE = 'tv_unreachable'
ERR_APP_NOT_RESPONDING = 'app_not_responding'
ERR_WRONG_FOREGROUND = 'wrong_foreground'
ERR_INVALID_PARAMETER = 'invalid_parameter'
ERR_OPERATION_TIMEOUT = 'operation_timeout'
ERR_MYTV_API_ERROR = 'mytv_api_error'
ERR_NOT_IMPLEMENTED = 'not_implemented'
ERR_CHANNEL_NO_NUMBER = 'channel_no_number'  # 频道无数字台号
ERR_APP_NOT_FOUND = 'app_not_found'  # 应用未安装
ERR_APP_LAUNCH_FAILED = 'app_launch_failed'  # 应用启动失败
ERR_INVALID_STATE = 'invalid_state'  # 工具级守卫：前台状态不符，零注入

__all__ = [
    'ERR_TV_UNREACHABLE',
    'ERR_APP_NOT_RESPONDING',
    'ERR_WRONG_FOREGROUND',
    'ERR_INVALID_PARAMETER',
    'ERR_OPERATION_TIMEOUT',
    'ERR_MYTV_API_ERROR',
    'ERR_NOT_IMPLEMENTED',
    'ERR_CHANNEL_NO_NUMBER',
    'ERR_APP_NOT_FOUND',
    'ERR_APP_LAUNCH_FAILED',
    'ERR_INVALID_STATE',
]
