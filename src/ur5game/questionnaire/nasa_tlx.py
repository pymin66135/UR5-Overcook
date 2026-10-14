"""六项 Raw NASA-TLX：保留每项评分和未加权平均，每项必须主动选择。"""
from datetime import datetime, timezone
import json
from pathlib import Path
from uuid import uuid4
import pygame as pg

TLX_ITEMS = (
    ("mental", "Mental Demand", "How mentally demanding was the task?", "Very Low", "Very High"),
    ("physical", "Physical Demand", "How physically demanding was the task?", "Very Low", "Very High"),
    ("temporal", "Temporal Demand", "How hurried or rushed was the pace of the task?", "Very Low", "Very High"),
    ("performance", "Performance", "How successful were you in accomplishing what you were asked to do?", "Perfect", "Failure"),
    ("effort", "Effort", "How hard did you have to work to accomplish your level of performance?", "Very Low", "Very High"),
    ("frustration", "Frustration", "How insecure, discouraged, irritated, stressed, and annoyed were you?", "Very Low", "Very High"),
)

BG = (15, 16, 18)
FG = (232, 232, 218)
DIM = (158, 158, 150)
GOLD = (215, 181, 80)
SIZE = (960, 760)


def now():
    """
    @brief  获取带 UTC 时区的记录时间。
    @param  None 无显式输入参数。
    @retval str ISO 8601 时间字符串。
    """
    return datetime.now(timezone.utc).isoformat()


def new_record(episode):
    """
    @brief  为本局创建未作答的六项问卷记录。
    @param  episode 本局唯一编号，用于问卷和游戏数据绑定。
    @retval dict 问卷记录；六项评分和总分初始为 None。
    """
    return {"episode_id": episode, "data_source": "INTERACTIVE_EXPERIENCE",
            "questionnaire": {"version": "raw_tlx_0_100_v1", "weighted": False,
                              "episode_id": episode, "status": "pending",
                              "scores": {item[0]: None for item in TLX_ITEMS},
                              "raw_tlx": None}, "created_at": now()}


def rate(record, episode, key, value):
    """
    @brief  登记一项显式选择的 NASA-TLX 评分。
    @param  record 当前问卷记录或结果记录字典。
    @param  episode 本局唯一编号，用于问卷和游戏数据绑定。
    @param  key 参数、数值区间或问卷维度的键名。
    @param  value 显式评分，整数 0–100 且为 5 的倍数。
    @retval None 更新评分和作答状态。
    @note   只接受 0–100 的整数且步长为 5；已提交记录不可修改。
    """
    q = record["questionnaire"]
    if record["episode_id"] != episode or q["status"] not in ("pending", "partial"):
        raise ValueError("Questionnaire binding/state mismatch")
    if key not in q["scores"] or type(value) is not int or value not in range(0, 101, 5):
        raise ValueError("Select a value from 0 to 100 in steps of 5")
    q["scores"][key] = value
    q.update(status="partial", updated_at=now())


def submit(record, episode):
    """
    @brief  校验六项全部作答并计算未加权平均分。
    @param  record 当前问卷记录或结果记录字典。
    @param  episode 本局唯一编号，用于问卷和游戏数据绑定。
    @retval None 问卷状态转 submitted，保存均值与时间。
    """
    q = record["questionnaire"]
    if (record["episode_id"] != episode or q["episode_id"] != episode
            or q["status"] not in ("pending", "partial")
            or any(v is None for v in q["scores"].values())):
        raise ValueError("All six ratings must be explicitly answered")
    q.update(status="submitted", raw_tlx=sum(q["scores"].values()) / 6, submitted_at=now())


def save_record(path, record):
    """
    @brief  通过临时文件替换方式保存当前问卷记录。
    @param  path 目标配置、记录或模型文件路径。
    @param  record 当前问卷记录或结果记录字典。
    @retval None 问卷文件更新完成。
    """
    path = Path(path)
    temporary = path.with_name(path.name + "." + uuid4().hex + ".tmp")
    with temporary.open("x", encoding="utf-8") as stream:
        json.dump(record, stream, ensure_ascii=False, indent=2, allow_nan=False)
    temporary.replace(path)


def text(screen, value, x, y, color=FG, size=19):
    """
    @brief  在问卷窗口指定位置绘制文本。
    @param  screen 问卷或独立体验窗口的画布。
    @param  value 要检查、编码或绘制的值，具体含义由当前函数决定。
    @param  x 文字或图标的画布横坐标，单位像素。
    @param  y 文字或图标的画布纵坐标，单位像素。
    @param  color RGB 文字颜色。
    @param  size 文字字号或图标边长，具体由绘制接口决定。
    @retval None 修改目标画布。
    """
    screen.blit(pg.font.SysFont("consolas", size).render(value, True, color), (x, y))


class TLXScreen:
    def __init__(self, batch, record):
        """
        @brief  绑定问卷记录和保存位置并初始化提示。
        @param  batch 问卷记录的保存路径。
        @param  record 当前问卷记录或结果记录字典。
        @retval None 默认所有评分未选择。
        """
        self.batch, self.record = batch, record
        self.message = "No rating is selected until you click its scale."

    def handle(self, event):
        """
        @brief  处理刻度点选、提交和退出事件。
        @param  event 界面事件，或环境要记录的事件类型。
        @retval str submitted/quit；None 表示继续显示问卷。
        """
        if event.type == pg.QUIT or event.type == pg.KEYDOWN and event.key == pg.K_ESCAPE:
            save_record(self.batch, self.record)
            return "quit"
        if event.type == pg.MOUSEBUTTONDOWN and event.button == 1:
            x, y = event.pos
            for i, item in enumerate(TLX_ITEMS):
                if 200 <= x <= 800 and 163 + i * 82 <= y <= 199 + i * 82:
                    value = max(0, min(100, round((x - 200) / 600 * 20) * 5))
                    rate(self.record, self.record["episode_id"], item[0], value)
                    save_record(self.batch, self.record)
        if event.type == pg.KEYDOWN and event.key == pg.K_RETURN:
            try:
                submit(self.record, self.record["episode_id"])
            except ValueError:
                self.message = "Please rate all six items. Missing answers are not zero."
            else:
                save_record(self.batch, self.record)
                return "submitted"

    def draw(self, screen):
        """
        @brief  绘制六项评分刻度、当前答案和操作说明。
        @param  screen 问卷或独立体验窗口的画布。
        @retval None 修改目标画布。
        """
        screen.fill(BG)
        text(screen, "Raw NASA-TLX | unweighted", 28, 20, GOLD, 25)
        if self.record.get("data_source") == "TEST":
            text(screen, "TEST - not participant data", 580, 25, GOLD, 17)
        text(screen, f"Rate the round you just played. Episode: {self.record['episode_id'][:12]}", 28, 61, DIM)
        text(screen, "Performance: 0 = Perfect, 100 = Failure (higher means worse).", 28, 88, GOLD, 17)
        for i, (key, title, question, low, high) in enumerate(TLX_ITEMS):
            y = 121 + i * 82
            text(screen, title, 28, y, FG, 18)
            text(screen, question, 205, y, DIM, 16)
            sy = y + 60
            pg.draw.line(screen, DIM, (200, sy), (800, sy), 2)
            for n in range(21):
                pg.draw.line(screen, DIM, (200 + n * 30, sy - 5), (200 + n * 30, sy + 5))
            value = self.record["questionnaire"]["scores"][key]
            if value is not None:
                pg.draw.circle(screen, GOLD, (200 + value * 6, sy), 7)
            text(screen, "Not rated" if value is None else str(value), 28, sy - 10, GOLD, 17)
            text(screen, f"0: {low}", 200, sy - 24, DIM, 14)
            text(screen, f"100: {high}", 675, sy - 24, DIM, 14)
        text(screen, self.message, 28, 648, GOLD, 16)
        text(screen, "Enter: submit all six ratings   Esc: save unfinished ratings and exit", 28, 690, FG, 17)
        text(screen, "Reflect on this round, not your general opinion of the partner.", 28, 720, DIM, 16)




def collect(path, episode, *, preview=None):
    """
    @brief  显示本局问卷或生成不记录答案的界面预览。
    @param  path 目标配置、记录或模型文件路径。
    @param  episode 本局唯一编号，用于问卷和游戏数据绑定。
    @param  preview 可选问卷预览图片路径；None 表示正常收集评分。
    @retval dict 已提交或未完成的问卷记录。
    @note   普通采集拒绝覆盖已存在目标文件；预览不写问卷数据。
    """
    path = Path(path)
    record = new_record(episode)
    if preview is None:
        path.parent.mkdir(parents=True, exist_ok=True)
        # 只创建新问卷，不覆盖其他回合的数据。
        with path.open("x", encoding="utf-8") as stream:
            json.dump(record, stream, ensure_ascii=False, indent=2)
    else:
        import os
        os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
        os.environ.setdefault("SDL_AUDIODRIVER", "dummy")
    pg.init()
    window = pg.display.set_mode(SIZE)
    pg.display.set_caption("Raw NASA-TLX")
    view = TLXScreen(path, record)
    clock = pg.time.Clock()
    try:
        if preview:
            view.draw(window)
            pg.display.flip()
            preview = Path(preview)
            preview.parent.mkdir(parents=True, exist_ok=True)
            pg.image.save(window, str(preview))
            return record
        while True:
            for event in pg.event.get():
                if view.handle(event) in ("quit", "submitted"):
                    return record
            view.draw(window)
            pg.display.flip()
            clock.tick(30)
    finally:
        pg.quit()
