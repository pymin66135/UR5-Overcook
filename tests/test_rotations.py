"""姿态跳变回归；不连接机器人。"""
import math
from dataclasses import replace
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from bootstrap import activate
activate()

from ur5game.robot.rotations import orientation_distance
from ur5game.robot.admittance import Admittance, SafetyFault
from ur5game.robot.types import Sample
from ur5game.config import load_experiment
from ur5game.robot.homing import load_start


class TestRotations(unittest.TestCase):
    def test_saved_origin_equivalent_branch_has_zero_error(self):
        """
        @brief  验证保存起点的等价旋转向量不会产生姿态误差。
        @param  None 无显式输入参数。
        @retval None 断言通过；失败由 unittest 报告。
        @note   仅用于离线测试，不连接机器人。
        """
        config = load_experiment()
        vector = load_start(config.source, config.robot_settings)[3:]
        magnitude = math.hypot(*vector)
        equivalent = tuple(v*(1.-2.*math.pi/magnitude) for v in vector)
        self.assertGreater(math.hypot(*(a-b for a,b in zip(vector,equivalent))), 6.)
        self.assertLess(orientation_distance(vector, equivalent), 1e-12)

    def test_pi_sign_flip_and_full_turn_are_equivalent(self):
        """
        @brief  验证 ±π 和整圈旋转表示同一姿态。
        @param  None 无显式输入参数。
        @retval None 断言通过；失败由 unittest 报告。
        @note   仅用于离线测试，不连接机器人。
        """
        self.assertLess(orientation_distance((math.pi,0.,0.),(-math.pi,0.,0.)), 1e-12)
        self.assertLess(orientation_distance((0.,0.,0.),(0.,0.,2.*math.pi)), 1e-12)

    def test_small_rotation_across_pi(self):
        """
        @brief  验证跨越 π 分支的小角度变化计算正确。
        @param  None 无显式输入参数。
        @retval None 断言通过；失败由 unittest 报告。
        @note   仅用于离线测试，不连接机器人。
        """
        self.assertAlmostEqual(orientation_distance((math.pi-.01,0.,0.),
                                                    (-math.pi+.01,0.,0.)), .02, places=12)

    def test_orthogonal_axes_and_tiny_angles(self):
        """
        @brief  验证不同旋转轴及极小旋转角的计算精度。
        @param  None 无显式输入参数。
        @retval None 断言通过；失败由 unittest 报告。
        @note   仅用于离线测试，不连接机器人。
        """
        self.assertAlmostEqual(orientation_distance((math.pi,0.,0.),(0.,math.pi,0.)), math.pi)
        self.assertAlmostEqual(orientation_distance((0.,0.,0.),(1e-10,0.,0.)), 1e-10, places=15)

    def test_invalid_vectors_fail(self):
        """
        @brief  验证非法或非有限姿态数据被拒绝。
        @param  None 无显式输入参数。
        @retval None 断言通过；失败由 unittest 报告。
        @note   仅用于离线测试，不连接机器人。
        """
        for vector in ((float("nan"),0.,0.),(float("inf"),0.,0.),(1.,2.),(1.7e308,)*3):
            with self.subTest(vector=vector), self.assertRaises(ValueError):
                orientation_distance(vector, (0.,0.,0.))

    def test_controller_accepts_equivalent_pose(self):
        """
        @brief  验证控制算法接受同一姿态的等价表示。
        @param  None 无显式输入参数。
        @retval None 断言通过；失败由 unittest 报告。
        @note   仅用于离线测试，不连接机器人。
        """
        config = load_experiment()
        pose = load_start(config.source, config.robot_settings)
        magnitude = math.hypot(*pose[3:])
        equivalent = pose[:3] + tuple(v*(1.-2.*math.pi/magnitude) for v in pose[3:])
        first = Sample(0.,pose,(0.,)*6,(0.,)*6,7,1)
        model = Admittance(config.limits, first, 0.)
        output = model.tick(replace(first, timestamp=.05, pose=equivalent),0,"wait")
        self.assertEqual(output.velocity_base, (0.,)*6)

    def test_controller_keeps_real_rotation_limit(self):
        """
        @brief  验证真实姿态变化仍遵守原角度停机阈值。
        @param  None 无显式输入参数。
        @retval None 断言通过；失败由 unittest 报告。
        @note   仅用于离线测试，不连接机器人。
        """
        config = load_experiment()
        limit = config.limits.max_rotation_rad
        first = Sample(0.,(0.,0.,.4,0.,0.,0.),(0.,)*6,(0.,)*6,7,1)
        for angle, should_fault in ((limit-.01,False),(limit+.01,True)):
            model = Admittance(config.limits, first, 0.)
            current = replace(first,timestamp=.05,pose=(0.,0.,.4,angle,0.,0.))
            if should_fault:
                with self.assertRaisesRegex(SafetyFault,"rotation"):
                    model.tick(current,0,"wait")
            else:
                self.assertEqual(model.tick(current,0,"wait").velocity_base,(0.,)*6)


if __name__ == "__main__":
    unittest.main()
