# ============================================================
#    配置文件 
# ============================================================

# ① 你的 API Key
API_KEY = ""

# ② URL
BASE_URL = ""

# ③ 使用的模型
#    参考：gemini-2.5-flash-lite / gpt-4o-mini / gpt-4o
MODEL = "gemini-2.5-flash-lite"

# ④ HTTP 代理
#    若已设置系统环境变量 HTTPS_PROXY，此项可留空
PROXY = ""

# 服务监听地址
HOST = "0.0.0.0"
PORT = 8000

# 文件限制
MAX_FILE_SIZE_MB = 50
FILE_EXPIRE_HOURS = 24

# 本地临时目录（自动创建）
UPLOAD_DIR = "uploads"
RESULT_DIR = "results"

# 每张幻灯片最多一次发送的段落数（避免超出 context window）
BATCH_SIZE = 80
