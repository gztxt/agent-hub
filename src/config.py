"""配置管理"""
import os
from pathlib import Path
from typing import Optional
from dotenv import load_dotenv

# 强制覆盖已有环境变量
env_path = Path(__file__).parent.parent / ".env"
if env_path.exists():
    load_dotenv(env_path, override=True)
    print(f"[Config] 已加载 .env（覆盖模式）: {env_path}")


class Config:
    """全局配置"""
    
    def __init__(self):
        # 强制使用 .env 中的值，即使环境变量已存在
        self.port = int(os.getenv("PORT", "3102"))
        self.host = os.getenv("HOST", "0.0.0.0")
        
        # Agent 端点
        self.ccr_url = os.getenv("CLAUDE_CCR_URL", "http://127.0.0.1:3456")
        self.pi_url = os.getenv("PI_WEB_URL", "http://127.0.0.1:30141")
        self.jcode_url = os.getenv("JCODE_URL", "http://127.0.0.1:3457")
        self.tdaI_url = os.getenv("TDAI_URL", "http://127.0.0.1:8420")
        
        # 认证
        self.ccr_auth = os.getenv("CCR_AUTH_TOKEN", "")
        self.pi_api_key = os.getenv("PI_API_KEY", "")
        
        # 数据路径
        self.data_dir = Path(os.getenv("DATA_DIR", Path.home() / "agent-hub" / "data"))
        self.log_dir = Path(os.getenv("LOG_DIR", self.data_dir / "logs"))
        
        # 确保目录存在
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.log_dir.mkdir(parents=True, exist_ok=True)
    
    @property
    def db_path(self) -> Path:
        return self.data_dir / "agents.db"
    
    @property
    def registry_path(self) -> Path:
        return self.data_dir / "registry.json"


config = Config()
