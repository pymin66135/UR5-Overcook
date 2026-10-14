"""组装实时游戏、已有 AI、UR5 服务和记录；入口不包含训练。"""
from datetime import datetime, timezone
import json
from pathlib import Path
from uuid import uuid4

from ..config import load_experiment
from ..game.ai_partner import AIPartner
from ..game.environment import load_config
from ..game.application import GameApp
from ..robot.service import RobotService


def run(config_path, *, ai="high", enable=False, output=None, human_role=None,
        seed=None, screenshot=None, questionnaire=None):
    """
    @brief  组装 AI、游戏和机器人服务，运行并保存本轮数据。
    @param  config_path 实验配置文件路径。
    @param  ai AI 等级，取 low 或 high。
    @param  enable 是否明确请求实机控制；False 不允许发送运动指令。
    @param  output 数据输出路径；None 时使用配置中的默认位置。
    @param  human_role 玩家初始角色 prep/kitchen；None 时采用配置。
    @param  seed 游戏及模型动作采样的随机种子；None 时采用入口配置。
    @param  screenshot 可选预览图片保存路径；None 表示正常显示界面。
    @param  questionnaire 是否结束后收集 NASA-TLX；None 时使用配置设置。
    @retval Path 本轮结果目录。
    @note   结束后可选填写問卷；enable=False 时不建立机器人连接。
    """
    if enable and screenshot:
        raise ValueError("UI preview cannot be combined with --enable")
    config = load_experiment(config_path)
    partner = AIPartner(config.model_manifest, load_config(config.scene), ai,
                        config.game["seed"] if seed is None else seed)
    episode = datetime.now().strftime("%Y%m%d_%H%M%S_") + uuid4().hex[:8]
    output = Path(output or config.results_dir) / "game" / episode
    output.mkdir(parents=True, exist_ok=False)
    partner.base_metadata.update(episode_id=episode,
                                 application="ur5_overcooked", config_snapshot=config.raw)
    if screenshot:
        partner.base_metadata["data_source"] = "UI_PREVIEW_NOT_EXPERIMENT"
    with (output / "config_snapshot.json").open("x", encoding="utf-8") as stream:
        json.dump(config.raw, stream, ensure_ascii=False, indent=2, allow_nan=False)
    result = None
    robot = None
    error = None
    try:
        robot = RobotService(config, output / "ur5", enable=enable)
        with robot:
            result = GameApp(config, partner, robot, output / "game",
                             human_role=human_role, screenshot=screenshot).run()
    except BaseException as exc:
        error = f"{type(exc).__name__}: {exc}"
        raise
    finally:
        status = {"episode_id": episode, "ai": ai, "live_requested": enable,
                  "ever_armed": robot.ever_armed if robot else False,
                  "fault": robot.fault if robot else error,
                  "error": error, "stop_reason": robot.stop_reason if robot else "startup_failed",
                  "completed_strokes": robot.cycle.completed if robot and robot.cycle else None,
                  "game": result, "formal_study": False,
                  "data_source": partner.base_metadata["data_source"],
                  "closed_utc": datetime.now(timezone.utc).isoformat(),
                  "questionnaire": {"status": "not_requested"}}
        with (output / "experience_status.json").open("x", encoding="utf-8") as stream:
            json.dump(status, stream, ensure_ascii=False, indent=2, allow_nan=False)
    collect = config.game["questionnaire_after_round"] if questionnaire is None else questionnaire
    if collect and not screenshot and robot and not robot.fault:
        from ..questionnaire.nasa_tlx import collect as collect_tlx
        record = collect_tlx(output / "nasa_tlx.json", episode)
        status["questionnaire"] = {"status": record["questionnaire"]["status"],
                                   "file": "nasa_tlx.json"}
        with (output / "experience_status.json").open("w", encoding="utf-8") as stream:
            json.dump(status, stream, ensure_ascii=False, indent=2, allow_nan=False)
    return output
