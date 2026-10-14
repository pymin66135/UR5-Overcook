"""从任意工作目录启动；优先使用随工程保存的游戏运行依赖。"""
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parent


def activate():
    """
    @brief  注册本工程代码和随工程保存的游戏依赖路径。
    @param  None 无显式输入参数。
    @retval None 无返回值；更新 sys.path。
    """
    for folder in (ROOT / "src", ROOT / "vendor"):
        value = str(folder)
        if value not in sys.path:
            sys.path.insert(0, value)
