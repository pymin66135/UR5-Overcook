"""旋转向量的姿态比较；返回真实最短旋转角，单位 rad。"""
import math


def _quaternion(rotation_vector):
    """
    @brief  将旋转向量转换为单位四元数。
    @param  rotation_vector 三个分量的 Axis-angle（轴角）旋转向量，单位 rad。
    @retval tuple 按 [w,x,y,z] 排列的四元数。
    @note   输入必须为三个有限数，旋转向量单位 rad。
    @note   Axis-angle（轴角）转换为单位四元数 [w,x,y,z]。
    """
    if len(rotation_vector) != 3 or not all(math.isfinite(v) for v in rotation_vector):
        raise ValueError("Orientation requires three finite rotation-vector values")
    angle = math.hypot(*rotation_vector)
    if not math.isfinite(angle):
        raise ValueError("Rotation-vector magnitude is not finite")
    if angle == 0.:
        return (1., 0., 0., 0.)
    scale = math.sin(angle / 2.) / angle
    q = (math.cos(angle / 2.), *(v * scale for v in rotation_vector))
    length = math.hypot(*q)
    return tuple(v / length for v in q)


def orientation_distance(first, second):
    """
    @brief  计算两种轴角表示之间的真实最短姿态旋转角。
    @param  first 第一个姿态的三维旋转向量，单位 rad。
    @param  second 第二个姿态的三维旋转向量，单位 rad。
    @retval float 范围 [0,π] 的最短角，单位 rad。
    @note   先对齐四元数符号，用 atan2 和弦长计算；等价 ±π/2π 表示不会造成跳变误报。
    @note   两姿态之间的最短角，范围 [0,π]，不受 ±π/2π 表示跳变影响。
    @note   q 和 -q 表示同一个姿态。先对齐四元数符号，再用弦长计算角度；
    @note   atan2 在极小角度和接近 π 时也稳定，不直接相减旋转向量。
    """
    a, b = _quaternion(first), _quaternion(second)
    if sum(x*y for x, y in zip(a, b)) < 0.:
        b = tuple(-v for v in b)
    difference = math.hypot(*(x-y for x, y in zip(a, b)))
    total = math.hypot(*(x+y for x, y in zip(a, b)))
    return 4. * math.atan2(difference, total)
