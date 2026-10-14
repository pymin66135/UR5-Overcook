"""Native Overcooked objects/interactions with opt-in ordered recipe timing.

Only pot admission, order accounting, and role exchange are scenario rules.
Movement, counter transfer, ingredient/plate pickup, plating and serving use
OvercookedGridworld. No role-based facility permissions, no teleportation.
"""
from __future__ import annotations

import copy
import json
import math
from collections import Counter, deque
from dataclasses import asdict, dataclass, field
from pathlib import Path

from overcooked_ai_py.mdp.actions import Action, Direction
from overcooked_ai_py.mdp.overcooked_mdp import EVENT_TYPES, OvercookedGridworld

CONFIG = Path(__file__).resolve().parents[3] / "configs" / "scene.json"
FOODS = ("onion", "tomato")


def load_config(path=CONFIG):
    """
    @brief  从 JSON 文件读取游戏场景配置。
    @param  path 目标配置、记录或模型文件路径。
    @retval dict 地图、配方和游戏时序配置。
    """
    return json.loads(Path(path).read_text(encoding="utf-8"))


@dataclass
class Pot:
    order: int | None = None
    phase: str = "empty"
    feed_ticks: list[int] = field(default_factory=list)
    open_tick: int | None = None
    deadline: int | None = None
    ready_tick: int | None = None


class ForcedCoopEnv:
    action_dim = 6
    observation_version = "forced-coop-v1"

    def __init__(self, config=None, human_role=None):
        """
        @brief  创建原生游戏环境、设施位置和本场景状态。
        @param  config 当前模块使用的配置字典。
        @param  human_role 玩家初始角色 prep/kitchen；None 时采用配置。
        @retval None 完成对象初始化。
        """
        self.config = copy.deepcopy(config if config is not None else load_config())
        self.human_role = human_role or self.config["control"]["human_role"]
        self._validate_config()
        self.mdp = OvercookedGridworld.from_grid(self.config["map"]["grid"], {
            "old_dynamics": True,
            "start_all_orders": [{"ingredients": ["onion", "onion", "tomato"]},
                                 {"ingredients": ["onion", "tomato", "tomato"]}]})
        self.floor = set(self.mdp.get_valid_player_positions())
        self.pot_positions = [tuple(p) for p in self.config["map"]["pots"]]
        self.shared = [tuple(p) for p in self.config["map"]["shared_counter"]]
        self.counter_positions = set(self.mdp.get_counter_locations())
        self._validate_map()
        self.reset()

    def _validate_config(self):
        """
        @brief  校验场景配方、时序、控制设置和地图配置。
        @param  None 无显式输入参数。
        @retval None 配置通过校验。
        """
        c = self.config
        for key in ("step_hz", "episode_seconds", "wait_seconds", "add_window_seconds", "final_cook_seconds"):
            if not isinstance(c[key], (int, float)) or not math.isfinite(c[key]) or c[key] <= 0:
                raise ValueError(f"{key} must be positive and finite")
            if abs(c[key] * c["step_hz"] - round(c[key] * c["step_hz"])) > 1e-8:
                raise ValueError(f"{key} must align with the fixed simulation tick")
        if self.human_role not in ("prep", "kitchen") or c["control"]["partner"] != "manual":
            raise ValueError("Choose prep/kitchen; this version has a manual P1 partner")
        if len(c["recipes"]) != 3 or any(len(v) != 3 or any(k not in FOODS for k in v)
                                         for v in c["recipes"].values()):
            raise ValueError("Exactly three recipes, each with three onion/tomato additions required")
        if not c["order_cycle"] or any(r not in c["recipes"] for r in c["order_cycle"]):
            raise ValueError("Order cycle must reference configured recipes")
        if type(c["pending_orders"]) is not int or c["pending_orders"] < 3:
            raise ValueError("At least three pending orders required")

    def _validate_map(self):
        """
        @brief  校验地图设施、角色位置和通道结构。
        @param  None 无显式输入参数。
        @retval None 地图通过校验。
        """
        if len(self.pot_positions) != 3 or set(self.pot_positions) != set(self.mdp.get_pot_locations()):
            raise ValueError("Three native P tiles must match map.pots")
        if len(self.shared) < 6 or len(set(self.shared)) != len(self.shared):
            raise ValueError("At least six distinct shared counter cells required")
        if len({x for x, _ in self.shared}) != 1:
            raise ValueError("Shared counter must be a vertical one-cell-thick line")
        ys = [y for _, y in self.shared]
        crossings = {tuple(p) for p in self.config["map"].get("crossing_cells", [])}
        if any(p not in self.floor for p in crossings):
            raise ValueError("Configured crossings must be walkable")
        if ys != sorted(ys) or set((self.shared[0][0], y) for y in range(min(ys), max(ys) + 1)) != set(self.shared) | crossings:
            raise ValueError("Shared counter and configured crossings must form an ordered line")
        for x, y in self.shared:
            if (x, y) not in self.counter_positions or (x - 1, y) not in self.floor or (x + 1, y) not in self.floor:
                raise ValueError("Every shared cell must be a normal counter with floor on both sides")
        reached = self.reachable(self.mdp.start_player_positions[0])
        if reached != self.floor:
            raise ValueError("Both rooms and every walkable cell must be connected")
        for pos in self.pot_positions + self.mdp.get_onion_dispenser_locations() + self.mdp.get_tomato_dispenser_locations() + self.mdp.get_dish_dispenser_locations() + self.mdp.get_serving_locations():
            if not any(Action.move_in_direction(pos, d) in reached for d in Direction.ALL_DIRECTIONS):
                raise ValueError(f"Unreachable facility: {pos}")
        blocked_rows = set(self.config["map"]["corridor_rows"])
        room_only = {p for p in self.floor if p[1] not in blocked_rows and p not in crossings}
        prep = next(p for p in room_only if self.room(p) == "prep")
        if any(self.room(p) == "kitchen" for p in self.reachable(prep, room_only)):
            raise ValueError("Rooms may connect only through configured crossings or corridor rows")

    def reachable(self, start, allowed=None):
        """
        @brief  搜索从给定起点可到达的网格位置。
        @param  start 初始机器人采样，用于建立位置和时间基准。
        @param  allowed 可选的允许访问网格集合；None 时使用地图可行走区域。
        @retval set 可到达的网格坐标集合。
        """
        allowed = self.floor if allowed is None else allowed
        seen, queue = {start}, deque([start])
        while queue:
            pos = queue.popleft()
            for d in Direction.ALL_DIRECTIONS:
                nxt = Action.move_in_direction(pos, d)
                if nxt in allowed and nxt not in seen:
                    seen.add(nxt)
                    queue.append(nxt)
        return seen

    def room(self, pos):
        """
        @brief  判断网格坐标所属的备菜区、厨房或通道。
        @param  pos 游戏网格坐标 (x,y)。
        @retval str 区域名称。
        """
        for role in ("prep", "kitchen"):
            x0, y0, x1, y1 = self.config["map"][f"{role}_bounds"]
            if x0 <= pos[0] <= x1 and y0 <= pos[1] <= y1:
                return role
        return "passage"

    @property
    def time(self):
        """
        @brief  读取当前游戏逻辑步数。
        @param  None 无显式输入参数。
        @retval int 原生状态中的 timestep。
        """
        return self.state.timestep

    def ticks(self, seconds):
        """
        @brief  将秒数转换为游戏逻辑步数。
        @param  seconds 持续时长，单位 s。
        @retval int 四舍五入后的逻辑步数。
        """
        return round(seconds * self.config["step_hz"])

    def reset(self, seed=None):
        """
        @brief  按种子重建订单、玩家和指标，开始新一局。
        @param  seed 游戏及模型动作采样的随机种子；None 时采用入口配置。
        @retval dict 重置后的游戏观察。
        """
        self.seed = self.config["seed"] if seed is None else int(seed)
        self.state = self.mdp.get_standard_start_state()
        if self.human_role == "prep":
            a, b = self.state.players
            a.position, b.position = b.position, a.position  # Initial placement only.
        self.roles = [self.human_role, "prep" if self.human_role == "kitchen" else "kitchen"]
        self.selected = [None, None]
        self.counter_guides = bool(self.config.get("counter_guides", True))
        self.pots = [Pot() for _ in self.pot_positions]
        self.orders, self.events = [], []
        self.score, self.uid = 0, 0
        self.metrics = [Counter(), Counter()]
        self.role_ticks = [Counter(), Counter()]
        self.dispensed, self.served, self.waste = Counter(), Counter(), Counter()
        self.last_placed = {}
        self.swap = {"status": None, "requested_tick": None, "start_tick": None,
                     "targets": None, "hide_tick": None}
        self.done = False
        self.feedback = [None, None]
        self._replenish_orders()
        self.emit("episode_start", roles=self.roles[:], seed=self.seed, controls=getattr(self, "control_modes", ["human", "manual"]))
        return self.observe()

    def emit(self, event, actor=None, **data):
        """
        @brief  向本局事件列表追加带游戏时间的行为事件。
        @param  event 界面事件，或环境要记录的事件类型。
        @param  actor 玩家索引 0/1；0 为人类 P0，1 为 AI P1。
        @param  data 要追加到事件记录中的扩展字段。
        @retval None 事件写入内存列表。
        """
        self.events.append({"tick": self.time, "seconds": self.time / self.config["step_hz"],
                            "event": event, "actor": actor, **data})

    def _replenish_orders(self):
        """
        @brief  按场景规则补充待完成订单。
        @param  None 无显式输入参数。
        @retval None 更新订单列表。
        """
        while sum(o["status"] == "pending" for o in self.orders) < self.config["pending_orders"]:
            i = len(self.orders)
            self.orders.append({"recipe": self.config["order_cycle"][i % len(self.config["order_cycle"])],
                                "status": "pending"})

    def select_recipe(self, actor, name):
        """
        @brief  设置指定玩家的配方选择。
        @param  actor 玩家索引 0/1；0 为人类 P0，1 为 AI P1。
        @param  name 物品或配方名称，依当前接口使用。
        @retval None 更新配方选择并记录事件。
        """
        if name is not None and name not in self.config["recipes"]:
            raise ValueError(name)
        self.selected[actor] = name
        self.emit("recipe_selected", actor, recipe=name)

    def toggle_guides(self):
        """
        @brief  切换柜台提示信息的显示状态。
        @param  None 无显式输入参数。
        @retval None 更新提示状态。
        """
        self.counter_guides = not self.counter_guides
        self.emit("counter_guides_changed", ordered_hints=self.counter_guides)

    def facing(self, actor):
        """
        @brief  计算玩家当前朝向的前方网格坐标。
        @param  actor 玩家索引 0/1；0 为人类 P0，1 为 AI P1。
        @retval tuple 玩家面前的 (x,y) 网格坐标。
        """
        p = self.state.players[actor]
        return Action.move_in_direction(p.position, p.orientation)

    def candidate_order(self, actor, ingredient):
        """
        @brief  查找与当前配方选择和首个食材匹配的待办订单。
        @param  actor 玩家索引 0/1；0 为人类 P0，1 为 AI P1。
        @param  ingredient 食材名称，如 onion 或 tomato。
        @retval int 匹配的订单索引；None 表示无匹配订单。
        """
        return next((i for i, order in enumerate(self.orders) if order["status"] == "pending"
                     and (self.selected[actor] is None or order["recipe"] == self.selected[actor])
                     and self.config["recipes"][order["recipe"]][0] == ingredient), None)

    def next_ingredient(self, pot):
        """
        @brief  查询锅中订单下一次需要加入的食材。
        @param  pot 锅的场景状态对象。
        @retval str 下一食材名称；None 表示无需继续投料。
        """
        if pot.order is None or pot.phase in ("burnt", "ready", "final"):
            return None
        return self.config["recipes"][self.orders[pot.order]["recipe"]][len(pot.feed_ticks)]

    def interaction_error(self, actor):
        """
        @brief  判断玩家当前交互是否满足设施和配方条件。
        @param  actor 玩家索引 0/1；0 为人类 P0，1 为 AI P1。
        @retval str 交互失败原因；None 表示允许交互。
        """
        pos = self.facing(actor)
        terrain = self.mdp.get_terrain_type_at_pos(pos)
        held = self.state.players[actor].held_object
        if terrain == "X":
            obj = self.state.objects.get(pos)
            return None if (held is None) != (obj is None) else ("Counter occupied" if obj else "Counter empty")
        if terrain in ("O", "T", "D"):
            return None if held is None else "Hands full"
        if terrain == "S":
            return None if held and held.name == "soup" else "Bring plated soup"
        if terrain != "P":
            return "No facility ahead"
        pot = self.pots[self.pot_positions.index(pos)]
        if pot.phase == "burnt":
            return None if held is None else "Empty hands to clean"
        if pot.phase == "ready":
            return None if held and held.name == "dish" else "Plate required"
        if pot.phase == "final":
            return "Final cooking"
        if held is None or held.name not in FOODS:
            return "Ingredient required"
        if pot.phase == "empty":
            return None if self.candidate_order(actor, held.name) is not None else "Wrong first ingredient / selected recipe"
        if held.name != self.next_ingredient(pot):
            return f"Wrong ingredient: need {self.next_ingredient(pot)}"
        if self.time < pot.open_tick:
            return "Too early"
        if self.time > pot.deadline:
            return "Too late"
        return None

    def legal_actions(self, actor):
        """
        @brief  生成指定玩家的六个原生动作有效性掩码。
        @param  actor 玩家索引 0/1；0 为人类 P0，1 为 AI P1。
        @retval list[bool] 动作有效标记；游戏结束时全部为 False。
        """
        if self.done:
            return [False] * 6
        return [True] * 5 + [self.interaction_error(actor) is None]

    def _native_interact(self, actor):
        """
        @brief  调用原生 Overcooked 交互规则处理指定玩家动作。
        @param  actor 玩家索引 0/1；0 为人类 P0，1 为 AI P1。
        @retval None 原地更新原生游戏状态。
        """
        actions = [Action.STAY, Action.STAY]
        actions[actor] = Action.INTERACT
        events = {name: [False, False] for name in EVENT_TYPES}
        self.mdp.resolve_interacts(self.state, tuple(actions), events)

    def _give_uid(self, obj):
        """
        @brief  为物品分配本局唯一标识。
        @param  obj 原生物品对象；None 表示不存在物品。
        @retval None 更新物品 task_uid。
        """
        self.uid += 1
        obj.task_uid = self.uid

    def interact(self, actor):
        """
        @brief  执行拾取、放置、投料、清理、装盘或交付并记录指标。
        @param  actor 玩家索引 0/1；0 为人类 P0，1 为 AI P1。
        @retval None 更新状态与事件；无效交互记录原因后返回。
        """
        error = self.interaction_error(actor)
        if error:
            category = "early_feed" if error == "Too early" else "wrong_feed" if error.startswith("Wrong") else "invalid_interaction"
            self.feedback[actor] = {"text": error, "until": self.time + self.ticks(1.5)}
            self.metrics[actor]["invalid_interactions"] += 1
            self.metrics[actor][category] += int(category != "invalid_interaction")
            self.emit(category, actor, reason=error, position=self.facing(actor))
            return
        pos = self.facing(actor)
        terrain = self.mdp.get_terrain_type_at_pos(pos)
        player = self.state.players[actor]
        held = player.held_object
        if terrain == "P":
            i = self.pot_positions.index(pos)
            pot = self.pots[i]
            if pot.phase == "burnt":
                self.state.remove_object(pos)
                self.pots[i] = Pot()
                self.metrics[actor]["cleanups"] += 1
                self.emit("pot_cleaned", actor, pot=i)
                return
            if pot.phase == "ready":
                self._native_interact(actor)
                soup = player.held_object
                soup.task_plated = True
                self.orders[pot.order]["status"] = "plated"
                self.emit("soup_plated", actor, pot=i, order=pot.order, item=soup.task_uid, plate=held.task_uid)
                self.pots[i] = Pot()
                return
            if pot.phase == "empty":
                pot.order = self.candidate_order(actor, held.name)
                self.orders[pot.order]["status"] = "cooking"
            self._native_interact(actor)
            soup = self.state.objects[pos]
            if len(pot.feed_ticks) == 0:
                self._give_uid(soup)
                soup.task_order = pot.order
            pot.feed_ticks.append(self.time)
            self.metrics[actor]["additions"] += 1
            self.emit("ingredient_added", actor, pot=i, order=pot.order, ingredient=held.name,
                      item=held.task_uid, addition=len(pot.feed_ticks))
            if len(pot.feed_ticks) < 3:
                pot.open_tick = self.time + self.ticks(self.config["wait_seconds"])
                pot.deadline = pot.open_tick + self.ticks(self.config["add_window_seconds"])
                pot.phase = f"wait{len(pot.feed_ticks) + 1}"
            else:
                pot.phase = "final"
                pot.open_tick = pot.deadline = None
                pot.ready_tick = self.time + self.ticks(self.config["final_cook_seconds"])
                soup._cook_time = self.ticks(self.config["final_cook_seconds"])
                soup.begin_cooking()
            self._replenish_orders()
        elif terrain == "X":
            before = held if held is not None else self.state.objects[pos]
            self._native_interact(actor)
            if held is not None:
                self.last_placed[before.task_uid] = actor
                self.metrics[actor]["placements"] += 1
                self.emit("counter_place", actor, position=pos, item=before.task_uid, name=before.name,
                          shared=pos in self.shared)
            else:
                self.metrics[actor]["pickups"] += 1
                self.emit("counter_pickup", actor, position=pos, item=before.task_uid, name=before.name,
                          shared=pos in self.shared)
                previous = self.last_placed.pop(before.task_uid, actor)
                if previous != actor:
                    self.metrics[actor]["handoffs_received"] += 1
                    self.emit("handoff", actor, sender=previous, position=pos, item=before.task_uid, name=before.name)
        elif terrain in ("O", "T", "D"):
            self._native_interact(actor)
            obj = player.held_object
            self._give_uid(obj)
            self.dispensed[obj.name] += 1
            self.emit("supply_pickup", actor, name=obj.name, item=obj.task_uid)
        elif terrain == "S":
            order = held.task_order
            self._native_interact(actor)
            self.orders[order]["status"] = "complete"
            self.served.update(held.ingredients)
            self.served["dish"] += 1
            self.score += self.config["order_score"]
            self.metrics[actor]["deliveries"] += 1
            self.emit("order_complete", actor, order=order, recipe=self.orders[order]["recipe"], item=held.task_uid)

    def request_swap(self, actor=0):
        """
        @brief  请求两名玩家交换角色。
        @param  actor 玩家索引 0/1；0 为人类 P0，1 为 AI P1。
        @retval bool True 请求被接受；False 表示当前条件不允许。
        """
        if self.done or self.swap["status"] in ("Requested", "Switching"):
            return False
        if any(self.room(p.position) != self.roles[i] for i, p in enumerate(self.state.players)):
            self.feedback[actor] = {"text": "Return to assigned rooms first", "until": self.time + self.ticks(2)}
            return False
        targets = ["prep" if r == "kitchen" else "kitchen" for r in self.roles]
        self.swap = {"status": "Requested", "requested_tick": self.time, "start_tick": None,
                     "targets": targets, "hide_tick": None}
        self.emit("swap_requested", actor, targets=targets, positions=[list(p) for p in self.state.player_positions])
        return True

    def cancel_swap(self, actor=0):
        """
        @brief  取消尚未完成的角色交换请求。
        @param  actor 玩家索引 0/1；0 为人类 P0，1 为 AI P1。
        @retval bool True 已取消；False 表示没有可取消请求。
        """
        if self.swap["status"] not in ("Requested", "Switching"):
            return False
        self.emit("swap_cancelled", actor, elapsed_seconds=(self.time - self.swap["requested_tick"]) / self.config["step_hz"])
        self.swap.update(status="Cancelled", targets=None, hide_tick=self.time + self.ticks(2))
        return True

    def _advance(self):
        """
        @brief  推进烹饪时序、订单和角色交换状态。
        @param  None 无显式输入参数。
        @retval None 更新本局时序状态。
        """
        for i, pot in enumerate(self.pots):
            if pot.phase.startswith(("wait", "add")):
                if self.time > pot.deadline:  # Both [5 s, 10 s] endpoints are inclusive.
                    soup = self.state.objects[self.pot_positions[i]]
                    self.waste.update(soup.ingredients)
                    self.orders[pot.order]["status"] = "pending"
                    self.emit("pot_failed", pot=i, order=pot.order, addition=len(pot.feed_ticks) + 1,
                              ingredients=soup.ingredients, deadline=pot.deadline)
                    pot.phase = "burnt"
                elif self.time >= pot.open_tick and pot.phase.startswith("wait"):
                    pot.phase = f"add{len(pot.feed_ticks) + 1}"
                    self.emit("add_window_open", pot=i, addition=len(pot.feed_ticks) + 1,
                              open_tick=pot.open_tick, deadline=pot.deadline)
            elif pot.phase == "final":
                soup = self.state.objects[self.pot_positions[i]]
                soup._cooking_tick = min(soup._cook_time, self.time - pot.feed_ticks[-1])
                if self.time >= pot.ready_tick:
                    pot.phase = "ready"
                    self.emit("pot_ready", pot=i, order=pot.order)
        if self.swap["status"] == "Requested":
            self.swap.update(status="Switching", start_tick=self.time)
            self.emit("swap_started", targets=self.swap["targets"])
        if self.swap["status"] == "Switching" and all(self.room(p.position) == self.swap["targets"][i]
                                                       for i, p in enumerate(self.state.players)):
            self.roles = self.swap["targets"][:]
            elapsed = (self.time - self.swap["requested_tick"]) / self.config["step_hz"]
            self.emit("swap_completed", roles=self.roles[:], elapsed_seconds=elapsed,
                      positions=[list(p) for p in self.state.player_positions])
            self.swap.update(status="Ready", hide_tick=self.time + self.ticks(2))
        if self.swap["hide_tick"] is not None and self.time >= self.swap["hide_tick"]:
            self.swap.update(status=None, targets=None, hide_tick=None)

    def step(self, actions):
        """
        @brief  执行双方动作并推进一个游戏逻辑步。
        @param  actions 双方动作索引序列。
        @retval tuple (观察, 得分增量, False, 是否结束, 本步事件信息)。
        """
        if self.done:
            raise RuntimeError("Episode finished; reset required")
        if len(actions) != 2 or any(type(a) is not int or a not in range(6) for a in actions):
            raise ValueError("Two action indices in 0..5 required")
        old_score, start = self.score, len(self.events)
        self.emit("joint_action", actions=list(actions))
        for i, p in enumerate(self.state.players):
            self.role_ticks[i][self.roles[i]] += 1
            self.metrics[i]["passage_ticks"] += int(self.room(p.position) == "passage")
        for i, a in enumerate(actions):
            if a == 5:
                self.interact(i)
        before = list(self.state.player_positions)
        self.mdp.resolve_movement(self.state, tuple(Action.INDEX_TO_ACTION[a] for a in actions))
        for i, p in enumerate(self.state.players):
            self.metrics[i]["movement_steps"] += int(p.position != before[i])
        self.state.timestep += 1
        self._advance()
        self.done = self.time >= self.ticks(self.config["episode_seconds"])
        if self.done:
            self.emit("episode_end", reason="time_limit")
        self.assert_conservation()
        return self.observe(), self.score - old_score, False, self.done, {"events": copy.deepcopy(self.events[start:])}

    @staticmethod
    def object_data(obj):
        """
        @brief  将原生物品转换为可记录的简明数据。
        @param  obj 原生物品对象；None 表示不存在物品。
        @retval dict 物品数据；None 表示空物品。
        """
        if obj is None:
            return None
        return {"name": obj.name, "uid": obj.task_uid, "position": obj.position,
                "ingredients": obj.ingredients if obj.name == "soup" else None,
                "order": getattr(obj, "task_order", None)}

    def accounted_resources(self):
        """
        @brief  统计当前已入账的食材、盘子等资源数量。
        @param  None 无显式输入参数。
        @retval Counter 各资源的数量统计。
        """
        total = self.served + self.waste
        objects = [(p.position, p.held_object, True) for p in self.state.players if p.held_object]
        objects += [(pos, obj, False) for pos, obj in self.state.objects.items()]
        for pos, obj, held in objects:
            if obj.name == "soup":
                if not held and pos in self.pot_positions and self.pots[self.pot_positions.index(pos)].phase == "burnt":
                    continue
                total.update(obj.ingredients)
                if getattr(obj, "task_plated", False):
                    total["dish"] += 1
            else:
                total[obj.name] += 1
        return total

    def assert_conservation(self):
        """
        @brief  检查供给、在场物品和消耗之间的资源守恒。
        @param  None 无显式输入参数。
        @retval None 守恒成立时返回；不成立触发断言。
        """
        assert self.accounted_resources() == self.dispensed, "Object duplication/loss"
        ids = [o.task_uid for o in self.state.objects.values()]
        ids += [p.held_object.task_uid for p in self.state.players if p.held_object]
        assert len(ids) == len(set(ids)), "Duplicate object ID"

    def observe(self):
        """
        @brief  构建供 AI 与日志使用的当前游戏观察副本。
        @param  None 无显式输入参数。
        @retval dict 包含玩家、地图、物品、订单和动作掩码的观察。
        """
        return copy.deepcopy({"version": self.observation_version, "tick": self.time,
            "remaining_seconds": max(0, self.config["episode_seconds"] - self.time / self.config["step_hz"]),
            "players": [{"position": p.position, "orientation": p.orientation, "held": self.object_data(p.held_object),
                         "role": self.roles[i], "room": self.room(p.position)} for i, p in enumerate(self.state.players)],
            "objects": [self.object_data(o) for o in self.state.objects.values()],
            "pots": [{**asdict(p), "next_ingredient": self.next_ingredient(p)} for p in self.pots],
            "orders": self.orders, "selected": self.selected, "counter_guides": self.counter_guides,
            "swap": self.swap, "score": self.score,
            "legal_actions": [self.legal_actions(i) for i in range(2)], "done": self.done,
            "map": self.config["map"], "recipes": self.config["recipes"],
            "timing": {k: self.config[k] for k in ("step_hz", "wait_seconds", "add_window_seconds", "final_cook_seconds")}})

    def summary(self):
        """
        @brief  汇总本局得分、订单、交互指标和资源记录。
        @param  None 无显式输入参数。
        @retval dict 本局结果摘要。
        """
        keys = ("placements", "pickups", "handoffs_received", "additions", "deliveries", "cleanups",
                "early_feed", "wrong_feed", "invalid_interactions", "movement_steps", "passage_ticks")
        return {"version": self.observation_version, "seed": self.seed, "ticks": self.time,
                "game_seconds": self.time / self.config["step_hz"], "score": self.score, "done": self.done,
                "completed_orders": sum(o["status"] == "complete" for o in self.orders),
                "agents": [{k: m[k] for k in keys} for m in self.metrics],
                "role_seconds": [{r: ticks / self.config["step_hz"] for r, ticks in v.items()} for v in self.role_ticks],
                "failures": sum(e["event"] == "pot_failed" for e in self.events),
                "swaps": [e for e in self.events if e["event"] == "swap_completed"],
                "dispensed": dict(self.dispensed), "accounted": dict(self.accounted_resources()),
                "waste": dict(self.waste), "partner_control": getattr(self, "control_modes", ["human", "manual"])[1]}
