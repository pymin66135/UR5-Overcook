"""唯一配置入口：按功能分组，加载一次后向各模块传递同一份设置。"""
from dataclasses import dataclass
import json
from pathlib import Path

from .robot.types import norm
from .robot.guards import resolve_policy, check_range
import math

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CONFIG = PROJECT_ROOT / "configs" / "experiment.json"


@dataclass(frozen=True)
class Limits:
    axis_base: tuple[float, float, float]
    travel_m: float
    mass_kg: float
    damping_n_s_m: tuple[float, float, float]
    max_speed_m_s: float
    max_accel_m_s2: float
    max_force_n: float
    max_lateral_m: float
    max_rotation_rad: float
    max_tracking_speed_m_s: float
    control_hz: float = 20.0
    force_deadband_n: float = 2.0
    force_filter_alpha: float = 0.2
    travel_margin_m: float = 0.002
    mass_levels_kg: tuple[float, float, float] | None = None
    stiffness_levels_n_m: tuple[float, float, float] | None = None
    planar_y_travel_m: float = 0.0
    planar_y_mass_kg: float = 1.0
    planar_y_damping_n_s_m: float = 80.0
    planar_y_stiffness_n_m: float = 0.0
    planar_y_center_speed_m_s: float = 0.01
    planar_y_center_tolerance_m: float = 0.0005
    safety_policy: dict | None = None

    def __post_init__(self):
        """
        @brief  校验限制参数、三档配置及数值策略的一致性。
        @param  None 无显式输入参数。
        @retval None 参数有效时完成初始化。
        """
        policy = resolve_policy(self.safety_policy, planar=self.planar_enabled)
        object.__setattr__(self, "safety_policy", policy)
        upper = policy["upper"]
        values = (self.travel_m, self.mass_kg, *self.damping_n_s_m,
                  self.max_speed_m_s, self.max_accel_m_s2, self.max_force_n,
                  self.max_lateral_m, self.max_rotation_rad,
                  self.max_tracking_speed_m_s, self.control_hz,
                  self.force_deadband_n, self.force_filter_alpha,
                  self.travel_margin_m)
        if len(self.axis_base) != 3 or not all(math.isfinite(x) for x in self.axis_base):
            raise ValueError("A finite base-frame axis is required")
        if abs(norm(self.axis_base) - 1.0) > 1e-3 or abs(self.axis_base[2]) > 0.1:
            raise ValueError("The push axis must be unit length and approximately horizontal")
        if len(self.damping_n_s_m) != 3 or not all(math.isfinite(x) and x > 0 for x in values[:12]):
            raise ValueError("Finite positive travel, mass, damping and safety limits are required")
        if sorted(self.damping_n_s_m) != list(self.damping_n_s_m):
            raise ValueError("Damping levels must be ascending")
        if (self.mass_levels_kg is None) != (self.stiffness_levels_n_m is None):
            raise ValueError("Mass and stiffness levels must be configured together")
        if self.mass_levels_kg is not None:
            if (len(self.mass_levels_kg) != 3 or len(self.stiffness_levels_n_m) != 3
                    or not all(math.isfinite(m) and 0 < m <= upper["mass_kg"] for m in self.mass_levels_kg)
                    or not all(math.isfinite(k) and 0 <= k <= upper["stiffness_n_m"] for k in self.stiffness_levels_n_m)
                    or sorted(self.stiffness_levels_n_m) != list(self.stiffness_levels_n_m)):
                raise ValueError("Invalid three-level mass/stiffness profile")
        if not 0 <= self.force_deadband_n < self.max_force_n:
            raise ValueError("Invalid force deadband")
        if not 0 < self.force_filter_alpha <= 1:
            raise ValueError("Invalid force filter")
        if not 0 <= self.travel_margin_m < self.travel_m / 4:
            raise ValueError("Invalid travel margin")
        if self.max_speed_m_s > self.max_tracking_speed_m_s:
            raise ValueError("Command speed must not exceed the tracking trip threshold")
        if (not math.isfinite(self.planar_y_travel_m) or not 0 <= self.planar_y_travel_m <= upper["planar_y_travel_m"]
                or not math.isfinite(self.planar_y_mass_kg) or not 0 < self.planar_y_mass_kg <= upper["mass_kg"]
                or not math.isfinite(self.planar_y_damping_n_s_m)
                or not policy["planar_y_damping_min_n_s_m"] <= self.planar_y_damping_n_s_m <= upper["damping_n_s_m"]):
            raise ValueError("Invalid XY admittance settings")
        if (not math.isfinite(self.planar_y_stiffness_n_m)
                or not 0 <= self.planar_y_stiffness_n_m <= upper["stiffness_n_m"]
                or not math.isfinite(self.planar_y_center_speed_m_s)
                or not 0 < self.planar_y_center_speed_m_s <= upper["planar_y_center_speed_m_s"]
                or not math.isfinite(self.planar_y_center_tolerance_m)
                or not 0 < self.planar_y_center_tolerance_m <= upper["planar_y_center_tolerance_m"]):
            raise ValueError("Invalid Y recentering settings")
        if self.planar_enabled and any(abs(a - b) > 1e-9
                                       for a, b in zip(self.axis_base, (-1., 0., 0.))):
            raise ValueError("XY mode requires base X- as the push axis; Z stays fixed")
        # Explicit JSON policy owns numeric ceilings; no second hidden cap.
        ceilings = {key: upper[key] for key in (
            "travel_m", "max_speed_m_s", "max_tracking_speed_m_s", "max_accel_m_s2",
            "max_force_n", "max_lateral_m", "max_rotation_rad")}
        exceeded = [f"{key}={getattr(self, key):g} > {ceiling:g}"
                    for key, ceiling in ceilings.items() if getattr(self, key) > ceiling]
        if self.damping_n_s_m[-1] > upper["damping_n_s_m"]:
            exceeded.append(f"damping_n_s_m[2]={self.damping_n_s_m[-1]:g} > {upper['damping_n_s_m']:g}")
        check_range(policy, "control_hz", self.control_hz)
        if exceeded:
            raise ValueError("UR5 pilot value exceeds safety_policy.upper: " + "; ".join(exceeded))

    @property
    def has_profile_levels(self) -> bool:
        """
        @brief  判断是否配置了三档虚拟质量与刚度。
        @param  None 无显式输入参数。
        @retval bool True 表示使用三档负载模式。
        """
        return self.mass_levels_kg is not None

    @property
    def planar_enabled(self) -> bool:
        """
        @brief  判断是否启用带 Y 范围的 XY 导纳模式。
        @param  None 无显式输入参数。
        @retval bool True 表示 Y 单侧范围大于零。
        """
        return self.planar_y_travel_m > 0

@dataclass(frozen=True)
class LoadProfile:
    """三档虚拟负载；质量单位 kg，阻尼 N*s/m，刚度 N/m。"""
    name: str
    mass_kg: float
    damping_n_s_m: float
    stiffness_n_m: float


@dataclass(frozen=True)
class ApplicationConfig:
    source: Path
    raw: dict
    robot_settings: dict
    limits: Limits
    load_profiles: tuple[LoadProfile, ...]
    scene: Path
    model_manifest: Path
    home_file: Path
    results_dir: Path
    game: dict
    interaction: dict


def load_experiment(path=DEFAULT_CONFIG):
    """
    @brief  读取分组配置，校验机器人参数并解析工程内资源路径。
    @param  path 目标配置、记录或模型文件路径。
    @retval ApplicationConfig 加载完成的配置对象。
    @note   相对路径以配置所属工程根目录解析。
    @note   所有相对路径以配置所属的工程目录为基准，不依赖终端工作目录。
    """
    path = Path(path).resolve()
    raw = json.loads(path.read_text(encoding="utf-8"))
    if raw.get("schema_version") != 1:
        raise ConfigurationError("Unsupported experiment schema_version")
    required = ("robot", "motion", "homing", "checks", "load_profiles", "interaction", "game", "paths")
    missing = [key for key in required if key not in raw]
    if missing:
        raise ConfigurationError("Missing configuration sections: " + ", ".join(missing))
    if (type(raw["game"].get("fps")) is not int or raw["game"]["fps"] <= 0
            or type(raw["game"].get("seed")) is not int
            or type(raw["game"].get("questionnaire_after_round")) is not bool):
        raise ConfigurationError("game requires positive integer fps, integer seed and boolean questionnaire_after_round")
    project = path.parent.parent
    profiles = tuple(LoadProfile(**item) for item in raw["load_profiles"])
    if len(profiles) != 3:
        raise ConfigurationError("load_profiles requires exactly three profiles")
    if any(not isinstance(profile.name, str) or not profile.name.strip() for profile in profiles):
        raise ConfigurationError("Each load profile requires a readable name")
    settings = {}
    for section in ("robot", "motion", "homing", "checks"):
        for key, value in raw[section].items():
            if key in settings:
                raise ConfigurationError(f"Duplicate parameter {key}")
            settings[key] = value
    settings.update(
        mass_levels_kg=[p.mass_kg for p in profiles],
        damping_n_s_m=[p.damping_n_s_m for p in profiles],
        stiffness_levels_n_m=[p.stiffness_n_m for p in profiles],
        q_ref_cells=raw["interaction"]["q_ref_cells"],
        game_endpoint_hold_s=raw["interaction"]["game_endpoint_hold_s"],
    )
    paths = {key: (project / value).resolve() for key, value in raw["paths"].items()}
    settings["fixed_start_file"] = str(paths["home"])
    settings, limits = validate_robot_settings(settings)
    return ApplicationConfig(path, raw, settings, limits, profiles,
                             paths["scene"], paths["models"], paths["home"],
                             paths["results"], raw["game"], raw["interaction"])


def load_robot_config(path=DEFAULT_CONFIG):
    """
    @brief  提取独立体验和游戏共用的机器人设置与限制。
    @param  path 目标配置、记录或模型文件路径。
    @retval tuple (机器人设置字典, Limits 参数对象)。
    @note   独立体验和游戏都经过同一个加载器，使用相同导纳参数。
    """
    config = load_experiment(path)
    return config.robot_settings, config.limits


class ConfigurationError(ValueError):
    pass


def validate_robot_settings(config):
    """
    @brief  校验机器人地址、工具、力来源和导纳限制参数。
    @param  config 当前模块使用的配置字典。
    @retval tuple (已校验设置字典, Limits 参数对象)。
    @note   不建立任何机器人连接。
    @note   将分组配置转换出的设置交给原控制算法校验。
    """
    required = ("host", "device_model", "controller_version", "tool_mass_kg",
                "tool_center_of_gravity_m", "tcp_pose_m_rad", "force_source",
                "axis_base", "travel_m", "mass_kg", "damping_n_s_m",
                "max_speed_m_s", "max_accel_m_s2", "max_force_n", "max_lateral_m",
                "max_rotation_rad", "max_tracking_speed_m_s", "control_hz",
                "q_ref_cells", "tool_calibration_verified", "force_calibration_verified",
                "pendant_safety_limits_verified", "controller_watchdog_verified")
    missing = [key for key in required if config.get(key) is None]
    if missing:
        raise ConfigurationError("Unconfigured UR5 fields: " + ", ".join(missing))
    if "safety_policy" in config and config["safety_policy"] is None:
        raise ConfigurationError("safety_policy must be an object, not null")
    config["safety_policy"] = resolve_policy(config.get("safety_policy"),
                                              planar=config.get("planar_y_travel_m", 0) > 0)
    if config["force_source"] not in ("ur5_internal_estimate", "hex_udp"):
        raise ConfigurationError("force_source must be ur5_internal_estimate or hex_udp")
    if config["force_source"] == "hex_udp":
        signs = config.get("hex_force_to_base_sign_xyz")
        if (not config.get("hex_sensor_host") or not isinstance(config.get("hex_sensor_port"), int)
                or not 1 <= config["hex_sensor_port"] <= 65535
                or not isinstance(signs, list) or len(signs) != 3
                or any(sign not in (-1, 1) for sign in signs)):
            raise ConfigurationError("Configure HEX host, port and base-frame signs")
        check_range(config["safety_policy"], "hex_timeout_s", config.get("hex_timeout_s", 0))
    if not config["host"] or not config["device_model"].startswith("UR5"):
        raise ConfigurationError("Expected a configured UR5 host and model")
    if len(config["tool_center_of_gravity_m"]) != 3 or len(config["tcp_pose_m_rad"]) != 6:
        raise ConfigurationError("Tool centre of gravity and TCP must have 3/6 entries")
    if not math.isfinite(config["tool_mass_kg"]) or config["tool_mass_kg"] <= 0:
        raise ConfigurationError("Tool mass must be calibrated")
    if not math.isfinite(config["q_ref_cells"]) or config["q_ref_cells"] <= 0:
        raise ConfigurationError("q_ref_cells must be positive")
    limits = Limits(**{key: config[key] for key in Limits.__dataclass_fields__ if key in config})
    return config, limits


def require_live_approval(config):
    """
    @brief  核对实机运行所需的人工确认标记。
    @param  config 当前模块使用的配置字典。
    @retval None 全部确认成立时正常返回。
    @note   缺少确认时抛出 ConfigurationError，不自动校准硬件。
    """
    approvals = ("tool_calibration_verified", "force_calibration_verified",
                 "pendant_safety_limits_verified", "controller_watchdog_verified")
    if config.get("planar_y_travel_m", 0) > 0:
        approvals += ("planar_workspace_verified", "planar_y_response_verified")
    pending = [key for key in approvals if config.get(key) is not True]
    if pending:
        raise ConfigurationError("Live motion refused; verify in the lab: " + ", ".join(pending))
