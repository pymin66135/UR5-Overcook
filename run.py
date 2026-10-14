"""统一入口：game、standalone、check、sensor-check、questionnaire。"""
import argparse
from pathlib import Path
from bootstrap import activate, ROOT

activate()


def parser():
    """
    @brief  构建统一命令行入口及各子命令参数。
    @param  None 无显式输入参数。
    @retval ArgumentParser 配置好的命令行解析器。
    """
    p = argparse.ArgumentParser(description="UR5 + Overcooked 实验运行与数据采集")
    sub = p.add_subparsers(dest="command", required=True)
    game = sub.add_parser("game", help="实时游戏，可选真实 UR5")
    game.add_argument("--config", type=Path, default=ROOT / "configs/experiment.json")
    game.add_argument("--ai", choices=("low", "high"), default="high")
    game.add_argument("--enable", action="store_true")
    game.add_argument("--output", type=Path)
    game.add_argument("--human-role", choices=("prep", "kitchen"))
    game.add_argument("--seed", type=int)
    game.add_argument("--screenshot", type=Path)
    game.add_argument("--questionnaire", action="store_true", default=None)
    # 子程序负责各自参数说明；此处只统一选择入口。
    for command, help_text in (("standalone", "独立三档 UR5 体验"),
                               ("check", "只读 UR5/HEX 基线检查"),
                               ("sensor-check", "只读 HEX 检查")):
        sub.add_parser(command, help=help_text, add_help=False)
    questionnaire = sub.add_parser("questionnaire", help="独立问卷或界面预览")
    questionnaire.add_argument("--episode", default="manual")
    questionnaire.add_argument("--output", type=Path)
    questionnaire.add_argument("--screenshot", type=Path)
    return p


def main(argv=None):
    """
    @brief  解析命令行并执行游戏、三档体验、只读检查或问卷入口。
    @param  argv 命令行参数列表；None 表示使用进程命令行。
    @retval None 正常完成无返回值。
    @note   argv 为 None 时读取当前进程命令行。
    """
    p = parser()
    args, remaining = p.parse_known_args(argv)
    if args.command == "game":
        if remaining:
            p.error("Unknown arguments: " + " ".join(remaining))
        if args.enable and args.screenshot:
            p.error("--screenshot cannot be combined with --enable")
        from ur5game.apps.realtime import run
        destination = run(args.config, ai=args.ai, enable=args.enable, output=args.output,
                          human_role=args.human_role, seed=args.seed,
                          screenshot=args.screenshot, questionnaire=args.questionnaire)
        print(f"Results: {destination}")
    elif args.command == "standalone":
        from ur5game.apps.standalone import main as entry
        entry(remaining)
    elif args.command == "check":
        from ur5game.apps.check import main as entry
        entry(remaining)
    elif args.command == "sensor-check":
        from ur5game.apps.sensor_check import main as entry
        entry(remaining)
    else:
        if remaining:
            p.error("Unknown arguments: " + " ".join(remaining))
        if not args.output and not args.screenshot:
            p.error("questionnaire requires --output for a new JSON or --screenshot for preview")
        from ur5game.questionnaire.nasa_tlx import collect
        collect(args.output or ROOT / "results/unused_preview.json", args.episode, preview=args.screenshot)


if __name__ == "__main__":
    main()
