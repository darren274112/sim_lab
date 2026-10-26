# ROSS Military Simulation

基于ROSS（Rensselaer's Optimistic Simulation System）的真实军事离散事件仿真。

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
