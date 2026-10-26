# ROSS Military Simulation

基于ROSS（Rensselaer's Optimistic Simulation System）的真实离散事件仿真。

## 场景：营级进攻战斗（Battalion Attack）

模拟蓝军营对红军防御阵地的进攻：
- 蓝军：坦克连、步兵连、炮兵连（共12个单位）
- 红军：防御排、反坦克组、火力点（共8个单位）
- 地形：开阔地+掩体
- 通信：受comms-service影响
- 火控：受fire-control影响
- 情报：受sensor-service影响

## LP（逻辑进程）设计
- UnitLP：作战单位（坦克、步兵等）
- SensorLP：传感器系统
- FireControlLP：火控系统
- CommLP：通信节点
- TerrainLP：地形环境

## 事件类型
- MOVE：机动
- DETECT：侦察发现
- ENGAGE：交战
- FIRE：射击
- DAMAGE：毁伤
- COMM_LOST：通信中断（故障注入点）

## 构建与运行

依赖 ROSS（默认安装在 `/opt/ross`）、CMake 和 MPI：

```bash
sudo apt-get install -y build-essential cmake mpich libmpich-dev
# 将 ROSS 安装到 /opt/ross 后：
ROSS_DIR=/opt/ross ./scripts/build.sh
UNITS=20 END=100 ./scripts/run_scenario.sh
```

当前 `ross-sim` 未实现乐观同步的反向计算，运行时使用顺序同步（`--synch=1`）。
容器内通过 `UCX_TLS=sm,self` 避免 MPI/UCX 选错网卡。

`sim-bridge` 镜像会在构建时自动完成上述 ROSS 和 `mil_sim` 的编译，无需在宿主机安装。

